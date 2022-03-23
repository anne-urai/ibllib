"""The widefield data extraction pipeline.

The widefield pipeline requires task data extraction using the FPGA (ephys_preprocessing),
optogenetics, camera extraction and widefield image data compression, SVD and correction.

Pipeline:
    1. Data renamed to be ALF-compliant and symlinks created with old names for use by wfield
    2. Raw image data is compressed
    3. Renamed and compressed files are registered to Alyx, imaging snapshots attached as Alyx notes
    4. Preprocessing run to produce
"""
import logging
from collections import OrderedDict
import traceback
from pathlib import Path
import packaging.version

import numpy as np
import pandas as pd
import labcams.io

import one.alf.io as alfio

from ibllib.misc import check_nvidia_driver
from ibllib.ephys import ephysqc, spikes, sync_probes
from ibllib.io import ffmpeg, spikeglx
from ibllib.io.video import label_from_path
from ibllib.io.extractors.widefield import Widefield as WidefieldExtractor
from ibllib.pipes import tasks
from ibllib.pipes.training_preprocessing import TrainingRegisterRaw as EphysRegisterRaw
from ibllib.pipes.ephys_preprocessing import (
    EphysPulses, EphysMtscomp, EphysAudio, EphysVideoCompress, EphysVideoSyncQc, EphysTrials, EphysPassive, EphysDLC, EphysPostDLC
)
from ibllib.oneibl.registration import register_session_raw_data
from ibllib.io.video import get_video_meta
from ibllib.pipes.misc import create_alyx_probe_insertions
from ibllib.qc.task_extractors import TaskQCExtractor
from ibllib.qc.task_metrics import TaskQC

_logger = logging.getLogger('ibllib')


class WidefieldRegisterRaw(tasks.Task):
    signature = {
        'input_files': [('dorsal_cortex_landmarks.json', 'raw_widefield_data', True),
                        ('*.camlog', 'raw_widefield_data', True)],
        'output_files': [('widefieldLandmarks.dorsalCortex.json', 'raw_widefield_data', True),
                         ('widefieldEvents.raw.camlog', 'raw_widefield_data', True)]
    }
    priority = 100

    def _run(self, overwrite=False):
        self.rename_files(symlink_old=True)
        self.register_snapshots()
        out_files, _ = register_session_raw_data(self.session_path, one=self.one, dry=True)
        return out_files

    def rename_files(self, symlink_old=True):
        """
        Rename the raw widefield data for a given session so that the data can be registered to Alyx.
        Keeping the old file names as symlinks is useful for preprocessing with the `wfield` module.

        Parameters
        ----------
        symlink_old : bool
            If True, create symlinks with the old filenames.

        """
        session_path = Path(self.session_path).joinpath('raw_widefield_data')
        if not session_path.exists():
            _logger.warning(f'Path does not exist: {session_path}')
            return
        for before, after in zip(self.input_files, self.output_files):
            old_file, old_collection, required = before
            old_path = self.session_path.rglob(str(Path(old_collection).joinpath(old_file)))
            old_path = next(old_path, None)
            if not old_path and not required:
                continue

            new_file, new_collection, _ = after
            new_path = self.session_path.joinpath(new_collection, new_file)
            old_path.replace(new_path)
            if symlink_old:
                old_path.symlink_to(new_path)

    def register_snapshots(self, unlink=False):
        """
        Register any photos in the snapshots folder to the session. Typically user will take photo of dorsal cortex before
        and after session

        Returns
        -------

        """
        snapshots_path = self.session_path.joinpath('raw_widefield_data', 'snapshots')
        if not snapshots_path.exists():
            return

        eid = self.one.path2eid(self.session_path, query_type='remote')
        if not eid:
            _logger.warning('Failed to upload snapshots: session not found on Alyx')
            return
        note = dict(user=self.one.alyx.user, content_type='session', object_id=eid, text='')

        notes = []
        for snapshot in snapshots_path.glob('*.tif'):
            with open(snapshot, 'rb') as img_file:
                files = {'image': img_file}
                notes.append(self.one.alyx.rest('notes', 'create', data=note, files=files))
            if unlink:
                snapshot.unlink()
        if unlink and next(snapshots_path.rglob('*'), None) is None:
            snapshots_path.rmdir()


class WidefieldCompress(tasks.Task):
    priority = 40
    level = 0
    force = False
    signature = {
        'input_files': [('*.dat', 'raw_widefield_data', True)],
        'output_files': [('widefield.raw.mov', 'raw_widefield_data', True)]
    }

    def _run(self, remove_uncompressed=False, verify_output=True, **kwargs):
        # Find raw data dat file
        filename, collection, _ = self.input_files[0]
        filepath = next(self.session_path.rglob(str(Path(collection).joinpath(filename))))

        # Construct filename for compressed video
        out_name, out_collection, _ = self.output_files[0]
        output_file = self.session_path.joinpath(out_collection, out_name)
        # Compress to mov
        stack = labcams.io.mmap_dat(str(filepath))
        labcams.io.stack_to_mj2_lossless(stack, str(output_file), rate=30)

        assert output_file.exists(), 'Failed to compress data: no output file found'

        if verify_output:
            meta = get_video_meta(output_file)
            assert meta.length > 0 and meta.size > 0, f'Video file empty: {output_file}

        if remove_uncompressed:
            filepath.unlink()

        return output_file


#  level 1
class WidefieldPreprocess(tasks.Task):
    priority = 60
    level = 1
    force = False
    signature = {
        'input_files': [('widefield.raw.*', 'raw_widefield_data', True),
                        ('widefieldEvents.raw.*', 'raw_widefield_data', True)],
        'output_files': [('widefieldChannels.frameAverage.npy', 'raw_widefield_data', True),
                         ('widefieldU.images.npy', 'alf', True),
                         ('widefieldSVT.uncorrected', 'alf', True),
                         ('widefieldSVT.haemoCorrected.npy', 'alf', True)]
    }

    def _run(self, **kwargs):
        self.wf = WidefieldExtractor(self.session_path)
        dsets, out_files = self.wf.extract(save=True)
        # rename files

        # QC could be run here
        return out_files

    def tearDown(self):
        super(WidefieldPreprocess, self).tearDown()
        self.wf.remove_files()


class WideFieldSync(tasks.Tasks):
    priority = 60
    level = 1
    force = False
    signature = {
        'input_files': [('widefield.raw.*', 'raw_widefield_data', True), # sync files for ephys data
                        ('widefieldEvents.raw.*', 'raw_widefield_data', True)],
        'output_files': [('widefield.times.npy', 'alf', True)]
    }

    def _run(self):

        self.wf = WidefieldExtractor(self.session_path)
        out_files = self.wf.sync_timestamps(bin_exists=False, save=True)

        return out_files





# pipeline
class WidefieldExtractionPipeline(tasks.Pipeline):
    label = __name__

    def __init__(self, session_path=None, **kwargs):
        super(WidefieldExtractionPipeline, self).__init__(session_path, **kwargs)
        tasks = OrderedDict()
        self.session_path = session_path
        # level 0
        for Task in (WidefieldRegisterRaw, WidefieldCompress, EphysPulses, WidefieldPreprocess, EphysAudio, EphysVideoCompress):
            task = Task(session_path)
            tasks[task.name] = task
        # level 1
        tasks["EphysTrials"] = EphysTrials(self.session_path, parents=[tasks["EphysPulses"]])
        tasks["EphysPassive"] = EphysPassive(self.session_path, parents=[tasks["EphysPulses"]])
        # level 2
        tasks["EphysVideoSyncQc"] = EphysVideoSyncQc(
            self.session_path, parents=[tasks["EphysVideoCompress"], tasks["EphysPulses"], tasks["EphysTrials"]])
        # tasks["EphysCellsQc"] = EphysCellsQc(self.session_path, parents=[tasks["SpikeSorting"]])
        tasks["EphysDLC"] = EphysDLC(self.session_path, parents=[tasks["EphysVideoCompress"]])
        # level 3
        tasks["EphysPostDLC"] = EphysPostDLC(self.session_path, parents=[tasks["EphysDLC"]])
        self.tasks = tasks