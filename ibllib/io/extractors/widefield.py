"""Data extraction from widefield binary file"""

import logging
import numpy as np
import shutil
from pathlib import Path
import pandas as pd

from neurodsp.utils import sync_timestamps
import ibllib.exceptions as err
import ibllib.io.extractors.base as extractors_base
from ibllib.io.extractors.ephys_fpga import get_sync_fronts, get_sync_and_chn_map
from ibllib.io.video import get_video_meta

import wfield.cli as wfield_cli
from labcams.io import parse_cam_log

_logger = logging.getLogger('ibllib')

"""Available LEDs for Widefield Imaging"""
LIGHT_SOURCE_MAP = {
    0: 'None',
    405: 'Violet',
    470: 'Blue',
}

DEFAULT_WIRING_MAP = {
    5: 470,
    6: 405
}


class Widefield(extractors_base.BaseExtractor):
    save_names = (None, None, None, 'widefieldChannels.frameAverage.npy', 'widefieldU.images.npy', 'widefieldSVT.uncorrected.npy',
                  None, None, 'widefieldSVT.haemoCorrected.npy', 'imaging.times.npy', 'imaging.imagingLightSource.npy',
                  'imagingLightSource.properties.htsv')
    raw_names = ('motioncorrect_2_540_640_uint16.bin', 'motion_correction_shifts.npy', 'motion_correction_rotation.npy',
                 'frames_average.npy', 'U.npy', 'SVT.npy', 'rcoeffs.npy', 'T.npy', 'SVTcorr.npy', 'timestamps.npy', 'led.npy',
                 'led_properties.htsv')
    var_names = ()

    def __init__(self, *args, **kwargs):
        """An extractor for all widefield data"""
        super().__init__(*args, **kwargs)
        self.data_path = self.session_path.joinpath('raw_widefield_data')
        self.default_path = 'alf/widefield'

    def _channel_meta(self, light_source_map=None):
        """
        Return table of light source wavelengths and corresponding colour labels.

        Parameters
        ----------
        light_source_map : dict
            An optional map of light source wavelengths (nm) used and their corresponding colour name.

        Returns
        -------
        pandas.DataFrame
            A sorted table of wavelength and colour name.
        """
        light_source_map = light_source_map or LIGHT_SOURCE_MAP
        names = ('wavelength', 'color')
        meta = pd.DataFrame(sorted(light_source_map.items()), columns=names)
        meta.index.rename('channel_id', inplace=True)
        return meta

    def _channel_wiring(self):
        try:
            wiring = pd.read_csv(self.data_path.joinpath('widefieldChannels.wiring.htsv'), sep='\t')
        except FileNotFoundError:
            _logger.warning('LED wiring map not found, using default')
            wiring = pd.DataFrame(DEFAULT_WIRING_MAP.items(), columns=('LED', 'wavelength'))

        return wiring

    def _extract(self, extract_timestamps=True, save=False, **kwargs):
        """
        NB: kwargs should be loaded from meta file
        Parameters
        ----------
        n_channels
        dtype
        shape
        kwargs

        Returns
        -------

        """
        self.preprocess(**kwargs)
        if extract_timestamps:
            _ = self.sync_timestamps(save=save)

        return None

    def _save(self, data=None, path_out=None):

        if not path_out:
            path_out = self.session_path.joinpath(self.default_path)
        path_out.mkdir(exist_ok=True, parents=True)

        new_files = []
        if not self.data_path.exists():
            _logger.warning(f'Path does not exist: {self.data_path}')
            return new_files

        for before, after in zip(self.raw_names, self.save_names):
            if after is None:
                continue
            else:
                try:
                    file_orig = next(self.data_path.glob(before))
                    file_new = path_out.joinpath(after)
                    shutil.move(file_orig, file_new)
                    new_files.append(file_new)
                except StopIteration:
                    _logger.warning(f'File not found: {before}')

        return new_files

    def preprocess(self, fs=30, functional_channel=0, nbaseline_frames=30, k=200):

        # MOTION CORRECTION
        wfield_cli._motion(str(self.data_path))
        # COMPUTE AVERAGE FOR BASELINE
        wfield_cli._baseline(str(self.data_path), nbaseline_frames)
        # DATA REDUCTION
        wfield_cli._decompose(str(self.data_path), k=k)
        # HAEMODYNAMIC CORRECTION
        # check if it is 2 channel
        dat = wfield_cli.load_stack(str(self.data_path))
        if dat.shape[1] == 2:
            del dat
            wfield_cli._hemocorrect(str(self.data_path), fs=fs, functional_channel=functional_channel)

    def remove_files(self, file_prefix='motion'):
        motion_files = self.data_path.glob(f'{file_prefix}*')
        for file in motion_files:
            _logger.info(f'Removing {file}')
            file.unlink()

    def sync_timestamps(self, bin_exists=False, save=False, save_paths=None, **kwargs):

        if save and save_paths:
            assert len(save_paths) == 3, 'Must provide save_path as list with 3 paths'
            for save_path in save_paths:
                Path(save_path).parent.mkdir(parents=True, exist_ok=True)

        # Load in fpga sync
        fpga_sync, chmap = get_sync_and_chn_map(self.session_path, 'raw_widefield_data')
        fpga_led = get_sync_fronts(fpga_sync, chmap['frame_trigger'])
        fpga_led_up = fpga_led['times'][fpga_led['polarities'] == 1]  # only consider up pulse times

        # Load in camlog sync
        logdata, led, sync, ncomm = parse_cam_log(next(self.data_path.glob('*.camlog')), readTeensy=True)
        assert led.frame.is_monotonic_increasing

        # Get video meta data to check number of widefield frames
        video_path = next(self.data_path.glob('imaging.frames*.mov'))
        video_meta = get_video_meta(video_path)

        # 1st: Check for differences between video and led
        diff = len(led) - video_meta.length
        if diff < 0:
            raise ValueError('More video frames than led frames detected')
        if diff > 2:
            raise ValueError('Led frames and video frames differ by more than 2')
        led = led[0:video_meta.length]
        led_times = led.timestamp.values / 1e3  # led timestamps are in ms

        # 2nd: Check for differences between daq detected led pulses and led frames
        # For now we don't tolerate but need to see how many fail this
        if led_times.size != fpga_led_up.size:
            _logger.warning(f'Sync mismatch by {np.abs(led_times.size - fpga_led_up.size)} '
                            f'NIDQ sync times: {fpga_led_up.size}, LED frame times {led_times.size}')
            raise ValueError('Sync mismatch')

        # If all okay, extract timestamps
        fcn, drift, iled, ifpga = sync_timestamps(led_times, fpga_led_up, return_indices=True)
        _logger.debug(f'Widefield-FPGA clock drift: {drift} ppm')
        widefield_times = fcn(led_times)
        assert np.all(np.diff(widefield_times) > 0)

        # Now extract the LED channels and meta data
        # Load channel meta and wiring map
        channel_meta_map = self._channel_meta(kwargs.get('light_source_map'))
        channel_wiring = self._channel_wiring()
        channel_id = np.empty_like(led.led.values)

        for _, d in channel_wiring.iterrows():
            mask = led.led.values == d['LED']
            if np.sum(mask) == 0:
                raise err.WidefieldWiringException
            channel_id[mask] = channel_meta_map.get(channel_meta_map['wavelength'] == d['wavelength']).index[0]

        if save:
            save_time = save_paths[0] if save_paths else self.data_path.joinpath('timestamps.npy')
            save_led = save_paths[1] if save_paths else self.data_path.joinpath('led.npy')
            save_meta = save_paths[2] if save_paths else self.data_path.joinpath('led_properties.htsv')
            save_paths = [save_time, save_led, save_meta]
            np.save(save_time, widefield_times)
            np.save(save_led, channel_id)
            channel_meta_map.to_csv(save_meta, sep='\t')

            return save_paths
        else:
            return widefield_times, channel_id, channel_meta_map