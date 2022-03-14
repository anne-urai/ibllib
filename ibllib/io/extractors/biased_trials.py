import numpy as np
from one.alf.io import AlfBunch

from ibllib.io.extractors.base import BaseBpodTrialsExtractor, run_extractor_classes
import ibllib.io.raw_data_loaders as raw
from ibllib.io.extractors.training_wheel import Wheel
from ibllib.io.extractors.training_trials import (
    Choice, FeedbackTimes, FeedbackType, GoCueTimes, GoCueTriggerTimes,
    IncludedTrials, Intervals, ItiDuration, ProbabilityLeft, ResponseTimes, RewardVolume,
    StimOnTimes_deprecated, StimOnTriggerTimes, StimOnOffFreezeTimes, ItiInTimes,
    StimOffTriggerTimes, StimFreezeTriggerTimes, ErrorCueTriggerTimes)
from ibllib.misc import version


class ContrastLR(BaseBpodTrialsExtractor):
    """
    Get left and right contrasts from raw datafile.
    """
    save_names = ('_ibl_trials.contrastLeft.npy', '_ibl_trials.contrastRight.npy')
    var_names = ('contrastLeft', 'contrastRight')

    def _extract(self, **kwargs):
        contrastLeft = np.array([t['contrast'] if np.sign(
            t['position']) < 0 else np.nan for t in self.bpod_trials])
        contrastRight = np.array([t['contrast'] if np.sign(
            t['position']) > 0 else np.nan for t in self.bpod_trials])
        return contrastLeft, contrastRight


class TrialsTable(BaseBpodTrialsExtractor):
    """
    Extracts the following into a table from Bpod raw data:
        intervals, goCue_times, response_times, choice, stimOn_times, contrastLeft, contrastRight,
        feedback_times, feedbackType, rewardVolume, probabilityLeft, firstMovement_times
    Additionally extracts the following wheel data:
        wheel_timestamps, wheel_position, wheel_moves_intervals, wheel_moves_peak_amplitude
    """
    save_names = ('_ibl_trials.table.pqt', '_ibl_wheel.timestamps.npy', '_ibl_wheel.position.npy',
                  '_ibl_wheelMoves.intervals.npy', '_ibl_wheelMoves.peakAmplitude.npy')
    var_names = ('table', 'wheel_timestamps', 'wheel_position', 'wheel_moves_intervals',
                 'wheel_moves_peak_amplitude')

    def _extract(self, extractor_classes=None, **kwargs):
        base = [Intervals, GoCueTimes, ResponseTimes, Choice, StimOnOffFreezeTimes, ContrastLR, FeedbackTimes, FeedbackType,
                RewardVolume, ProbabilityLeft, Wheel]
        exclude = [
            'stimOff_times', 'stimFreeze_times', 'wheel_timestamps', 'wheel_position',
            'wheel_moves_intervals', 'wheel_moves_peak_amplitude', 'peakVelocity_times', 'is_final_movement'
        ]
        out, _ = run_extractor_classes(base,
            session_path=self.session_path, bpod_trials=self.bpod_trials, settings=self.settings, save=False
        )
        table = AlfBunch({k: v for k, v in out.items() if k not in exclude})
        assert len(table.keys()) == 12

        return table.to_df(), *(out.pop(x) for x in self.var_names if x != 'table')


def extract_all(session_path, save=False, bpod_trials=False, settings=False):
    """
    Same as training_trials.extract_all except...
     - there is no RepNum
     - ContrastLR is extracted differently
     - IncludedTrials is only extracted for 5.0.0 or greater

    :param session_path:
    :param save:
    :param bpod_trials:
    :param settings:
    :param extra_classes: additional BaseBpodTrialsExtractor subclasses for custom extractions
    :return:
    """
    if not bpod_trials:
        bpod_trials = raw.load_data(session_path)
    if not settings:
        settings = raw.load_settings(session_path)
    if settings is None or settings['IBLRIG_VERSION_TAG'] == '':
        settings = {'IBLRIG_VERSION_TAG': '100.0.0'}

    base = [GoCueTriggerTimes]
    # Version check
    if version.ge(settings['IBLRIG_VERSION_TAG'], '5.0.0'):
        # We now extract a single trials table
        base.extend([
            StimOnTriggerTimes, ItiInTimes, StimOffTriggerTimes, StimFreezeTriggerTimes, ErrorCueTriggerTimes, TrialsTable,
            IncludedTrials
        ])
    else:
        base.extend([
            Intervals, Wheel, FeedbackType, ContrastLR, ProbabilityLeft, Choice, ItiDuration,
            StimOnTimes_deprecated, RewardVolume, FeedbackTimes, ResponseTimes, GoCueTimes
        ])

    out, fil = run_extractor_classes(
        base, save=save, session_path=session_path, bpod_trials=bpod_trials, settings=settings)
    return out, fil
