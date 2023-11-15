"""Habituation ChoiceWorld Bpod trials extraction."""
import logging
import numpy as np

import ibllib.io.raw_data_loaders as raw
from ibllib.io.extractors.base import BaseBpodTrialsExtractor, run_extractor_classes
from ibllib.io.extractors.biased_trials import ContrastLR
from ibllib.io.extractors.training_trials import FeedbackTimes, StimOnTriggerTimes, GoCueTimes

_logger = logging.getLogger(__name__)


class HabituationTrials(BaseBpodTrialsExtractor):
    var_names = ('feedbackType', 'rewardVolume', 'stimOff_times', 'contrastLeft', 'contrastRight',
                 'feedback_times', 'stimOn_times', 'stimOnTrigger_times', 'intervals',
                 'goCue_times', 'goCueTrigger_times', 'itiIn_times', 'stimOffTrigger_times',
                 'stimCenterTrigger_times', 'stimCenter_times', 'position', 'phase')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        exclude = ['itiIn_times', 'stimOffTrigger_times', 'stimCenter_times',
                   'stimCenterTrigger_times', 'position', 'phase']
        self.save_names = tuple(f'_ibl_trials.{x}.npy' if x not in exclude else None for x in self.var_names)

    def _extract(self) -> dict:
        """
        Extract the Bpod trial events.

        The Bpod state machine for this task has extremely misleading names! The 'iti' state is
        actually the delay between valve open and trial end (the stimulus is still present during
        this period), and the 'trial_start' state is actually the ITI during which there is a 1s
        Bpod TTL and gray screen period.

        Returns
        -------
        dict
            A dictionary of Bpod trial events. The keys are defined in the `var_names` attribute.
        """
        # Extract all trials...

        # Get all detected TTLs. These are stored for QC purposes
        self.frame2ttl, self.audio = raw.load_bpod_fronts(self.session_path, data=self.bpod_trials)
        # These are the frame2TTL pulses as a list of lists, one per trial
        ttls = [raw.get_port_events(tr, 'BNC1') for tr in self.bpod_trials]

        # Report missing events
        n_missing = sum(len(pulses) != 3 for pulses in ttls)
        # Check if all stim syncs have failed to be detected
        if n_missing == len(ttls):
            _logger.error(f'{self.session_path}: Missing ALL BNC1 TTLs ({n_missing} trials)')
        elif n_missing > 0:  # Check if any stim_sync has failed be detected for every trial
            _logger.warning(f'{self.session_path}: Missing BNC1 TTLs on {n_missing} trial(s)')

        # Extract datasets common to trainingChoiceWorld
        training = [ContrastLR, FeedbackTimes, GoCueTimes, StimOnTriggerTimes]
        out, _ = run_extractor_classes(training, session_path=self.session_path, save=False,
                                       bpod_trials=self.bpod_trials, settings=self.settings, task_collection=self.task_collection)

        """
        The 'trial_start' state is in fact the 1s grey screen period, therefore the first timestamp
        is really the end of the previous trial and also the stimOff trigger time. The second
        timestamp is the true trial start time.
        """
        (_, *ends), starts = zip(*[
            t['behavior_data']['States timestamps']['trial_start'][-1] for t in self.bpod_trials]
        )

        # StimOffTrigger times
        out['stimOffTrigger_times'] = np.array(ends)

        # StimOff times
        """
        There should be exactly three TTLs per trial.  stimOff_times should be the first TTL pulse.
        If 1 or more pulses are missing, we can not be confident of assigning the correct one.
        """
        out['stimOff_times'] = np.array([sync[0] if len(sync) == 3 else np.nan
                                         for sync, off in zip(ttls[1:], ends)])

        # Trial intervals
        """
        In terms of TTLs, the intervals are defined by the 'trial_start' state, however the stim
        off time often happens after the trial end TTL front, i.e. after the 'trial_start' start
        begins.  For these trials, we set the trial end time as the stim off time.
        """
        # NB: We lose the last trial because the stim off event occurs at trial_num + 1
        n_trials = out['stimOff_times'].size
        out['intervals'] = np.c_[starts, np.r_[ends, np.nan]][:n_trials, :]
        to_update = out['intervals'][:, 1] < out['stimOff_times']
        out['intervals'][to_update, 1] = out['stimOff_times'][to_update]

        # itiIn times
        out['itiIn_times'] = np.r_[ends, np.nan]

        # GoCueTriggerTimes is the same event as StimOnTriggerTimes
        out['goCueTrigger_times'] = out['stimOnTrigger_times'].copy()

        # StimCenterTrigger times
        # Get the stim_on_state that triggers the onset of the stim
        stim_center_state = np.array([tr['behavior_data']['States timestamps']
                                      ['stim_center'][0] for tr in self.bpod_trials])
        out['stimCenterTrigger_times'] = stim_center_state[:, 0].T

        # StimCenter times
        stim_center_times = np.full(out['stimCenterTrigger_times'].shape, np.nan)
        for i, (sync, last) in enumerate(zip(ttls, out['stimCenterTrigger_times'])):
            """We expect there to be 3 pulses per trial; if this is the case, stim center will
            be the third pulse. If any pulses are missing, we can only be confident of the correct
            one if exactly one pulse occurs after the stim center trigger"""
            if len(sync) == 3 or (len(sync) > 0 and sum(pulse > last for pulse in sync) == 1):
                stim_center_times[i] = sync[-1]
        out['stimCenter_times'] = stim_center_times

        # StimOn times
        stimOn_times = np.full(out['stimOnTrigger_times'].shape, np.nan)
        for i, (sync, last) in enumerate(zip(ttls, out['stimCenterTrigger_times'])):
            """We expect there to be 3 pulses per trial; if this is the case, stim on will be the
            second pulse. If 1 pulse is missing, we can only be confident of the correct one if
            both pulses occur before the stim center trigger"""
            if len(sync) == 3 or (len(sync) == 2 and sum(pulse < last for pulse in sync) == 2):
                stimOn_times[i] = sync[1]
        out['stimOn_times'] = stimOn_times

        # RewardVolume
        trial_volume = [x['reward_amount'] for x in self.bpod_trials]
        out['rewardVolume'] = np.array(trial_volume).astype(np.float64)

        # FeedbackType is always positive
        out['feedbackType'] = np.ones(len(out['feedback_times']), dtype=np.int8)

        # Phase and position
        out['position'] = np.array([t['position'] for t in self.bpod_trials])
        out['phase'] = np.array([t['stim_phase'] for t in self.bpod_trials])

        # Double-check that the early and late trial events occur within the trial intervals
        idx = ~np.isnan(out['stimOn_times'][:n_trials])
        if np.any(out['stimOn_times'][:n_trials][idx] < out['intervals'][idx, 0]):
            _logger.warning('Stim on events occurring outside trial intervals')
        idx = ~np.isnan(out['stimOff_times'])
        if np.any(out['stimOff_times'][idx] > out['intervals'][idx, 1]):
            _logger.warning('Stim off events occurring outside trial intervals')

        # Truncate arrays and return in correct order
        return {k: out[k][:n_trials] for k in self.var_names}


def extract_all(session_path, save=False, bpod_trials=False, settings=False, task_collection='raw_behavior_data', save_path=None):
    """Extract all datasets from habituationChoiceWorld
    Note: only the datasets from the HabituationTrials extractor will be saved to disc.

    :param session_path: The session path where the raw data are saved
    :param save: If True, the datasets that are considered standard are saved to the session path
    :param bpod_trials: The raw Bpod trial data
    :param settings: The raw Bpod sessions
    :returns: a dict of datasets and a corresponding list of file names
    """
    if not bpod_trials:
        bpod_trials = raw.load_data(session_path, task_collection=task_collection)
    if not settings:
        settings = raw.load_settings(session_path, task_collection=task_collection)

    # Standard datasets that may be saved as ALFs
    params = dict(session_path=session_path, bpod_trials=bpod_trials, settings=settings, task_collection=task_collection,
                  path_out=save_path)
    out, fil = run_extractor_classes(HabituationTrials, save=save, **params)
    return out, fil
