"""Base Extractor classes.

A module for the base Extractor classes.  The Extractor, given a session path, will extract the
processed data from raw hardware files and optionally save them.
"""

import abc
from collections import OrderedDict
import json
from pathlib import Path

import numpy as np
import pandas as pd
from ibllib.io import raw_data_loaders as raw
from ibllib.io.raw_data_loaders import load_settings, _logger


class BaseExtractor(abc.ABC):
    """
    Base extractor class
    Writing an extractor checklist:
    -   on the child class, overload the _extract method
    -   this method should output one or several numpy.arrays or dataframe with a consistent shape
    -   save_names is a list or a string of filenames, there should be one per dataset
    -   set save_names to None for a dataset that doesn't need saving (could be set dynamically
    in the _extract method)
    :param session_path: Absolute path of session folder
    :type session_path: str/Path
    """

    session_path = None
    """pathlib.Path: Absolute path of session folder."""

    save_names = None
    """tuple of str: The filenames of each extracted dataset, or None if array should not be saved."""

    var_names = None
    """tuple of str: A list of names for the extracted variables. These become the returned output keys."""

    default_path = Path('alf')  # relative to session
    """pathlib.Path: The default output folder relative to `session_path`."""

    def __init__(self, session_path=None):
        # If session_path is None Path(session_path) will fail
        self.session_path = Path(session_path)

    def extract(self, save=False, path_out=None, **kwargs):
        """
        :return: dict of numpy.array, list of filenames
        """
        out = self._extract(**kwargs)
        files = self._save(out, path_out=path_out) if save else None
        return out, files

    def _save(self, data, path_out=None):
        # Check if self.save_names is of the same length of out
        if not path_out:
            path_out = self.session_path.joinpath(self.default_path)

        def _write_to_disk(file_path, data):
            """Implements different save calls depending on file extension.

            Parameters
            ----------
            file_path : pathlib.Path
                The location to save the data.
            data : pandas.DataFrame, numpy.ndarray
                The data to save

            """
            csv_separators = {
                ".csv": ",",
                ".ssv": " ",
                ".tsv": "\t"
            }
            # Ensure empty files are not created; we expect all datasets to have a non-zero size
            if getattr(data, 'size', len(data)) == 0:
                filename = file_path.relative_to(self.session_path).as_posix()
                raise ValueError(f'Data for {filename} appears to be empty')
            file_path = Path(file_path)
            file_path.parent.mkdir(exist_ok=True, parents=True)
            if file_path.suffix == ".npy":
                np.save(file_path, data)
            elif file_path.suffix in [".parquet", ".pqt"]:
                if not isinstance(data, pd.DataFrame):
                    _logger.error("Data is not a panda's DataFrame object")
                    raise TypeError("Data is not a panda's DataFrame object")
                data.to_parquet(file_path)
            elif file_path.suffix in csv_separators:
                sep = csv_separators[file_path.suffix]
                data.to_csv(file_path, sep=sep)
                # np.savetxt(file_path, data, delimiter=sep)
            else:
                _logger.error(f"Don't know how to save {file_path.suffix} files yet")

        if self.save_names is None:
            file_paths = []
        elif isinstance(self.save_names, str):
            file_paths = path_out.joinpath(self.save_names)
            _write_to_disk(file_paths, data)
        elif isinstance(data, dict):
            file_paths = []
            for var, value in data.items():
                if fn := self.save_names[self.var_names.index(var)]:
                    fpath = path_out.joinpath(fn)
                    _write_to_disk(fpath, value)
                    file_paths.append(fpath)
        else:  # Should be list or tuple...
            assert len(data) == len(self.save_names)
            file_paths = []
            for data, fn in zip(data, self.save_names):
                if fn:
                    fpath = path_out.joinpath(fn)
                    _write_to_disk(fpath, data)
                    file_paths.append(fpath)
        return file_paths

    @abc.abstractmethod
    def _extract(self):
        pass


class BaseBpodTrialsExtractor(BaseExtractor):
    """
    Base (abstract) extractor class for bpod jsonable data set
    Wrps the _extract private method

    :param session_path: Absolute path of session folder
    :type session_path: str
    :param bpod_trials
    :param settings
    """

    bpod_trials = None
    settings = None
    task_collection = None
    frame2ttl = None
    audio = None

    def extract(self, bpod_trials=None, settings=None, **kwargs):
        """
        :param: bpod_trials (optional) bpod trials from jsonable in a dictionary
        :param: settings (optional) bpod iblrig settings json file in a dictionary
        :param: save (bool) write output ALF files, defaults to False
        :param: path_out (pathlib.Path) output path (defaults to `{session_path}/alf`)
        :return: numpy.ndarray or list of ndarrays, list of filenames
        :rtype: dtype('float64')
        """
        self.bpod_trials = bpod_trials
        self.settings = settings
        self.task_collection = kwargs.pop('task_collection', 'raw_behavior_data')
        if self.bpod_trials is None:
            self.bpod_trials = raw.load_data(self.session_path, task_collection=self.task_collection)
        if not self.settings:
            self.settings = raw.load_settings(self.session_path, task_collection=self.task_collection)
        if self.settings is None:
            self.settings = {"IBLRIG_VERSION": "100.0.0"}
        elif self.settings.get("IBLRIG_VERSION", "") == "":
            self.settings["IBLRIG_VERSION"] = "100.0.0"
        return super(BaseBpodTrialsExtractor, self).extract(**kwargs)


def run_extractor_classes(classes, session_path=None, **kwargs):
    """
    Run a set of extractors with the same inputs.

    :param classes: list of Extractor class
    :param save: True/False
    :param path_out: (defaults to alf path)
    :param kwargs: extractor arguments (session_path...)
    :return: dictionary of arrays, list of files
    """
    files = []
    outputs = OrderedDict({})
    assert session_path
    # if a single class is passed, convert as a list
    try:
        iter(classes)
    except TypeError:
        classes = [classes]
    for classe in classes:
        cls = classe(session_path=session_path)
        out, fil = cls.extract(**kwargs)
        if isinstance(fil, list):
            files.extend(fil)
        elif fil is not None:
            files.append(fil)
        if isinstance(out, dict):
            outputs.update(out)
        elif isinstance(cls.var_names, str):
            outputs[cls.var_names] = out
        else:
            for i, k in enumerate(cls.var_names):
                outputs[k] = out[i]
    return outputs, files


def _get_task_types_json_config():
    """
    Return the extractor types map.

    This function is only used for legacy sessions, i.e. those without an experiment description
    file and will be removed in favor of :func:`_get_task_extractor_map`, which directly returns
    the Bpod extractor class name. The experiment description file cuts out the need for pipeline
    name identifiers.

    Returns
    -------
    Dict[str, str]
        A map of task protocol to task extractor identifier, e.g. 'ephys', 'habituation', etc.

    See Also
    --------
    _get_task_extractor_map - returns a map of task protocol to Bpod trials extractor class name.
    """
    with open(Path(__file__).parent.joinpath('extractor_types.json')) as fp:
        task_types = json.load(fp)
    try:
        # look if there are custom extractor types in the personal projects repo
        import projects.base
        custom_extractors = Path(projects.base.__file__).parent.joinpath('extractor_types.json')
        _logger.debug('Loading extractor types from %s', custom_extractors)
        with open(custom_extractors) as fp:
            custom_task_types = json.load(fp)
        task_types.update(custom_task_types)
    except (ModuleNotFoundError, FileNotFoundError):
        pass
    return task_types


def get_task_protocol(session_path, task_collection='raw_behavior_data'):
    """
    Return the task protocol name from task settings.

    If the session path and/or task collection do not exist, the settings file is missing or
    otherwise can not be parsed, or if the 'PYBPOD_PROTOCOL' key is absent, None is returned.
    A warning is logged if the session path or settings file doesn't exist. An error is logged if
    the settings file can not be parsed.

    Parameters
    ----------
    session_path : str, pathlib.Path
        The absolute session path.
    task_collection : str
        The session path directory containing the task settings file.

    Returns
    -------
    str or None
        The Pybpod task protocol name or None if not found.
    """
    try:
        settings = load_settings(session_path, task_collection=task_collection)
    except json.decoder.JSONDecodeError:
        _logger.error(f'Can\'t read settings for {session_path}')
        return
    if settings:
        return settings.get('PYBPOD_PROTOCOL', None)
    else:
        return


def get_task_extractor_type(task_name):
    """
    Returns the task type string from the full pybpod task name.

    Parameters
    ----------
    task_name : str
        The complete task protocol name from the PYBPOD_PROTOCOL field of the task settings.

    Returns
    -------
    str
        The extractor type identifier. Examples include 'biased', 'habituation', 'training',
        'ephys', 'mock_ephys' and 'sync_ephys'.

    Examples
    --------
    >>> get_task_extractor_type('_iblrig_tasks_biasedChoiceWorld3.7.0')
    'biased'

    >>> get_task_extractor_type('_iblrig_tasks_trainingChoiceWorld3.6.0')
    'training'
    """
    if isinstance(task_name, Path):
        task_name = get_task_protocol(task_name)
        if task_name is None:
            return
    task_types = _get_task_types_json_config()

    task_type = task_types.get(task_name, None)
    if task_type is None:  # Try lazy matching of name
        task_type = next((task_types[tt] for tt in task_types if tt in task_name), None)
    if task_type is None:
        _logger.warning(f'No extractor type found for {task_name}')
    return task_type


def get_session_extractor_type(session_path, task_collection='raw_behavior_data'):
    """
    Infer trials extractor type from task settings.

    From a session path, loads the settings file, finds the task and checks if extractors exist.
    Examples include 'biased', 'habituation', 'training', 'ephys', 'mock_ephys', and 'sync_ephys'.
    Note this should only be used for legacy sessions, i.e. those without an experiment description
    file.

    Parameters
    ----------
    session_path : str, pathlib.Path
        The session path for which to determine the pipeline.
    task_collection : str
        The session path directory containing the raw task data.

    Returns
    -------
    str or False
        The task extractor type, e.g. 'biased', 'habituation', 'ephys', or False if unknown.
    """
    task_protocol = get_task_protocol(session_path, task_collection=task_collection)
    if task_protocol is None:
        _logger.error(f'ABORT: No task protocol found in "{task_collection}" folder {session_path}')
        return False
    extractor_type = get_task_extractor_type(task_protocol)
    if extractor_type:
        return extractor_type
    else:
        return False


def get_pipeline(session_path, task_collection='raw_behavior_data'):
    """
    Get the pre-processing pipeline name from a session path.

    Note this is only suitable for legacy sessions, i.e. those without an experiment description
    file. This function will be removed in the future.

    Parameters
    ----------
    session_path : str, pathlib.Path
        The session path for which to determine the pipeline.
    task_collection : str
        The session path directory containing the raw task data.

    Returns
    -------
    str
        The pipeline name inferred from the extractor type, e.g. 'ephys', 'training', 'widefield'.
    """
    stype = get_session_extractor_type(session_path, task_collection=task_collection)
    return _get_pipeline_from_task_type(stype)


def _get_pipeline_from_task_type(stype):
    """
    Return the pipeline from the task type.

    Some task types directly define the pipeline. Note this is only suitable for legacy sessions,
    i.e. those without an experiment description file. This function will be removed in the future.

    Parameters
    ----------
    stype : str
        The session type or task extractor type, e.g. 'habituation', 'ephys', etc.

    Returns
    -------
    str
        A task pipeline identifier.
    """
    if stype in ['ephys_biased_opto', 'ephys', 'ephys_training', 'mock_ephys', 'sync_ephys']:
        return 'ephys'
    elif stype in ['habituation', 'training', 'biased', 'biased_opto']:
        return 'training'
    elif isinstance(stype, str) and 'widefield' in stype:
        return 'widefield'
    else:
        return stype or ''


def _get_task_extractor_map():
    """
    Load the task protocol extractor map.

    Returns
    -------
    Dict[str, str]
        A map of task protocol to Bpod trials extractor class.
    """
    FILENAME = 'task_extractor_map.json'
    with open(Path(__file__).parent.joinpath(FILENAME)) as fp:
        task_extractors = json.load(fp)
    try:
        # look if there are custom extractor types in the personal projects repo
        import projects.base
        custom_extractors = Path(projects.base.__file__).parent.joinpath(FILENAME)
        with open(custom_extractors, 'r') as fp:
            custom_task_types = json.load(fp)
        task_extractors.update(custom_task_types)
    except (ModuleNotFoundError, FileNotFoundError):
        pass
    return task_extractors


def get_bpod_extractor_class(session_path, task_collection='raw_behavior_data'):
    """
    Get the Bpod trials extractor class associated with a given Bpod session.

    Note that unlike :func:`get_session_extractor_type`, this function maps directly to the Bpod
    trials extractor class name. This is hardware invariant and is purly to determine the Bpod only
    trials extractor.

    Parameters
    ----------
    session_path : str, pathlib.Path
        The session path containing Bpod behaviour data.
    task_collection : str
        The session_path sub-folder containing the Bpod settings file.

    Returns
    -------
    str
        The extractor class name.
    """
    # Attempt to get protocol name from settings file
    protocol = get_task_protocol(session_path, task_collection=task_collection)
    if not protocol:
        raise ValueError(f'No task protocol found in {Path(session_path) / task_collection}')
    return protocol2extractor(protocol)


def protocol2extractor(protocol):
    """
    Get the Bpod trials extractor class associated with a given Bpod task protocol.

    The Bpod task protocol can be found in the 'PYBPOD_PROTOCOL' field of the
    _iblrig_taskSettings.raw.json file.

    Parameters
    ----------
    protocol : str
        A Bpod task protocol name.

    Returns
    -------
    str
        The extractor class name.
    """
    # Attempt to get extractor class from protocol
    extractor_map = _get_task_extractor_map()
    extractor = extractor_map.get(protocol, None)
    if extractor is None:  # Try lazy matching of name
        extractor = next((extractor_map[tt] for tt in extractor_map if tt in protocol), None)
    if extractor is None:
        raise ValueError(f'No extractor associated with "{protocol}"')
    return extractor
