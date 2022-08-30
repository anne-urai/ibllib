"""A module for handling experiment description files.

Each device computer adds its piece of information and consolidates into the final acquisition
description.

The purpose is 3-fold:
    - provide modularity in the extraction: the acquisition description allows to dynamically build
    pipelines.
    - assist the copying of the experimental data from device computers to the server computers, in
    a way that each device is independent from another.
    - assist the copying of the experimental data from device computers to the server computers, in
    a way that intermediate states (failed copies) are easily recoverable from and completion
    criteria (ie. session ready to extract) is objective and simple (all device files copied).

INGRESS
    - each device computer needs to know the session path on the server.
    - create a device file locally in a queue directory. This will serve as a copy flag.
    - copy the device file to the local server.

EGRESS
    - got through the queue and for each item:
        - if the device file is not on the server create it.
        - once copy is complete aggregate the qc from file.
"""
import yaml
import time
from datetime import datetime
import logging
from pathlib import Path
import warnings

from pkg_resources import parse_version

from ibllib.pipes.misc import create_basic_transfer_params


_logger = logging.getLogger(__name__)
SPEC_VERSION = '0.1.0'


def write_yaml(file_path, data):
    """
    Write a device file. This is basically just a yaml dump that ensures the folder tree exists.

    Parameters
    ----------
    file_path : pathlib.Path
        The full path to the description yaml file to write to.
    data : dict
        The data to write to the yaml file.

    """
    file_path.parent.mkdir(exist_ok=True, parents=True)
    with open(file_path, 'w+') as fp:
        yaml.safe_dump(data, fp)


def _patch_file(data: dict) -> dict:
    """
    Update older description data to conform to the most recent specification.

    Parameters
    ----------
    data : dict
        The description yaml data.

    Returns
    -------
    dict
        The patched description data.
    """
    if data and (v := data.get('version', '0')) != SPEC_VERSION:
        if parse_version(v) > parse_version(SPEC_VERSION):
            _logger.warning('Description file generated by more recent code')
        data['version'] = SPEC_VERSION
    return data


def read_params(path):
    """
    Load an experiment description file.

    In addition to reading the yaml data, this functions ensures that the specification is the most
    recent one.  If the file is missing None is returned.  If the file cannot be parsed an empty
    dict is returned.

    Parameters
    ----------
    path : pathlib.Path, str
        The path to the description yaml file (or it's containing folder) to load.

    Returns
    -------
    dict, None
        The parsed yaml data, or None if the file was not found.

    Examples
    --------
    # Load a session's _ibl_experiment.description.yaml file

    >>> data = read_params('/home/data/subject/2020-01-01/001')

    # Load a specific device's description file

    >>> data = read_params('/home/data/subject/2020-01-01/001/_devices/behaviour.yaml')

    """
    if (path := Path(path)).is_dir():
        yaml_file = next(path.glob('_ibl_experiment.description*'), None)
    else:
        yaml_file = path if path.exists() else None
    if not yaml_file:
        _logger.debug('Experiment description not found: %s', path)
        return

    with open(yaml_file, 'r') as fp:
        data = _patch_file(yaml.safe_load(fp) or {})
    return data


def aggregate_device(file_device, file_acquisition_description, unlink=False):
    """
    Add the contents of a device file to the main acquisition description file.

    Parameters
    ----------
    file_device : pathlib.Path
        The full path to the device yaml file to add to the main description file.
    file_acquisition_description : pathlib.Path
        The full path to the main acquisition description yaml file to add the device file to.
    unlink : bool
        If True, the device file is removed after successfully aggregation.

    Raises
    ------
    AssertionError
        Device file contains a main 'sync' key that is already present in the main description
        file.  For an experiment only one main sync device is allowed.
    """
    # if a lock file exists retries 5 times to see if it exists
    attempts = 0
    file_lock = file_acquisition_description.with_suffix('.lock')
    # reads in the partial device data
    data_device = read_params(file_device)

    if not data_device:
        _logger.warning('empty device file "%s"', file_device)
        return

    while True:
        if not file_lock.exists() or attempts >= 4:
            break
        _logger.info('file lock found, waiting 2 seconds %s', file_lock)
        time.sleep(2)
        attempts += 1

    # if the file still exists after 5 attempts, remove it as it's a job that went wrong
    if file_lock.exists():
        with open(file_lock, 'r') as fp:
            _logger.debug('file lock contents: %s', yaml.safe_load(fp))
        _logger.info('stale file lock found, deleting %s', file_lock)
        file_lock.unlink()

    # add in the lock file, add some meta data to ease debugging if one gets stuck
    with open(file_lock, 'w') as fp:
        yaml.safe_dump(dict(datetime=datetime.utcnow().isoformat(), file_device=str(file_device)), fp)

    # if the acquisition description file already exists, read in the yaml content
    if file_acquisition_description.exists():
        acq_desc = read_params(file_acquisition_description)
    else:
        acq_desc = {}

    # merge the dictionaries
    for k in data_device:
        if k == 'sync':
            assert k not in acq_desc, 'multiple sync fields defined'
        if isinstance(data_device[k], list):
            acq_desc[k] = acq_desc.get(k, []) + data_device[k]
        elif isinstance(data_device[k], dict):
            acq_desc[k] = {**acq_desc.get(k, {}), **data_device[k]}

    with open(file_acquisition_description, 'w') as fp:
        yaml.safe_dump(acq_desc, fp)

    # unlink the local file
    file_lock.unlink()
    # delete the original file if necessary
    if unlink:
        file_device.unlink()


def get_cameras(sess_params):
    devices = sess_params.get('devices', {})
    cameras = devices.get('cameras', None)
    return None if not cameras else list(cameras.keys())


def get_sync(sess_params):
    sync = sess_params.get('sync', None)
    if not sync:
        return None
    else:
        (sync, _), = sync.items()
    return sync


def get_sync_collection(sess_params):
    sync = sess_params.get('sync', None)
    if not sync:
        return None
    else:
        (_, sync_details), = sync.items()
    return sync_details.get('collection', None)


def get_sync_extension(sess_params):
    sync = sess_params.get('sync', None)
    if not sync:
        return None
    else:
        (_, sync_details), = sync.items()
    return sync_details.get('extension', None)


def get_sync_namespace(sess_params):
    sync = sess_params.get('sync', None)
    if not sync:
        return None
    else:
        (_, sync_details), = sync.items()
    return sync_details.get('acquisition_software', None)


def get_task_protocol(sess_params, task_collection):
    protocols = sess_params.get('tasks', None)
    if not protocols:
        return None
    else:
        protocol = None
        for prot, details in sess_params.get('tasks').items():
            if details.get('collection') == task_collection:
                protocol = prot

        return protocol


def get_task_collection(sess_params):
    protocols = sess_params.get('tasks', None)
    if not protocols:
        return None
    elif len(protocols) > 1:
        return 'raw_behavior_data'
    else:
        for prot, details in protocols.items():
            return details['collection']


def get_device_collection(sess_params, device):
    # TODO
    return None


def get_video_compressed(sess_params):
    videos = sess_params.get('devices', {}).get('cameras', None)
    if not videos:
        return None

    # This is all of nothing, assumes either all videos or not compressed
    for key, vals in videos.items():
        compressed = vals.get('compressed', False)

    return compressed


def prepare_experiment(session_path, acquisition_description=None, local=None, remote=None):
    """
    Copy acquisition description yaml to the server and local transfers folder.

    Parameters
    ----------
    session_path : str, pathlib.Path, pathlib.PurePath
        The RELATIVE session path, e.g. subject/2020-01-01/001.
    """
    # Determine if user passed in arg for local/remote subject folder locations or pull in from
    # local param file or prompt user if missing
    params = create_basic_transfer_params(transfers_path=local, remote_data_path=remote)

    # First attempt to copy to server
    remote_device_path = Path(params['REMOTE_DATA_FOLDER_PATH']).joinpath(session_path, '_devices')
    try:
        for label, data in acquisition_description.items():
            write_yaml(remote_device_path.joinpath(f'{label}.yaml'), data)
    except Exception as ex:
        warnings.warn(f'Failed to write data to {remote_device_path}: {ex}')

    # Now copy to local directory
    local_device_path = Path(params['TRANSFERS_PATH']).joinpath(session_path)
    for label, data in acquisition_description.items():
        write_yaml(local_device_path.joinpath(f'{label}.yaml'), data)
