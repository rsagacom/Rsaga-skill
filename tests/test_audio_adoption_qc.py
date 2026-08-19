"""P1 音频链/候选采用 + P2 自动 QC 测试。"""

from __future__ import annotations

import unittest

from studio_core.adoption import (
    CandidateKind,
    CandidateStatus,
    InvariantViolation,
    ShotAdoption,
    ShotCandidate,
    assert_adoption_invariants,
)
from studio_core.audio import (
    AudioTrack,
    DialogueCue,
    MixSpec,
    SubtitleCue,
    TrackKind,
    VoiceCasting,
    audio_gate,
)
from studio_core.qc import MediaEvidence, ManualDecision, QCResult, run_auto_qc


class VoiceCastingTests(unittest.TestCase):
    def test_voice_casting_dict_has_consent_ref(self):
        casting = VoiceCasting(
            casting_id="VC_001",
            character_id="CHAR_001",
            voice_name="qiyuan_cosy3",
            consent_ref="RIGHTS_AND_LICENSES.md#voice-char-001",
        )
        data = casting.to_dict()
        self.assertEqual(data["voice_name"], "qiyuan_cosy3")
        self.assertEqual(data["consent_ref"], "RIGHTS_AND_LICENSES.md#voice-char-001")

    def test_dialogue_cue_asr_verification(self):
        cue = DialogueCue(
            cue_id="CUE_1",
            shot_id="SHOT_001_003_001",
            character_id="CHAR_001",
            casting_id="VC_001",
            text="刚才那里，明明有人。",
            start_sec=8.5,
            duration_sec=2.2,
        )
        self.assertIsNone(cue.asr_match())

        cue.asr_transcript = "刚才那里,明明有人。"
        self.assertTrue(cue.asr_match())

        cue.asr_transcript = "刚才那里明明没有人。"
        self.assertFalse(cue.asr_match())

    def test_dialogue_density_cps(self):
        cue = DialogueCue(
            cue_id="CUE_2",
            shot_id="S1",
            character_id="C1",
            casting_id="VC_1",
            text="你早就知道了？",
            start_sec=0.0,
            duration_sec=1.0,
        )
        self.assertAlmostEqual(cue.cps, 7.0)


class AudioTrackAndGateTests(unittest.TestCase):
    def test_audio_gate_rejects_missing_dialogue_track(self):
        issues = audio_gate([], [])
        self.assertTrue(any("no dialogue" in i for i in issues))

    def test_audio_gate_passes_with_tracks_and_valid_subtitles(self):
        tracks = [
            AudioTrack(
                track_id="T1",
                kind=TrackKind.DIALOGUE,
                media_asset_id="ASSET_dialogue_wav",
                source_note="CosyVoice3",
                gain_db=0.0,
            ),
            AudioTrack(
                track_id="T2",
                kind=TrackKind.AMBIENT,
                media_asset_id="ASSET_ambient_bed",
                source_note="H3-native",
                gain_db=-14.0,
                dip_to_background=True,
            ),
        ]
        subs = [
            SubtitleCue(cue_id="S1", start_sec=1.0, end_sec=2.2, text="刚才那里，明明有人。", speaker="祁元思远")
        ]
        self.assertEqual(audio_gate(tracks, subs), [])

    def test_mix_spec_reports_clipping(self):
        mix = MixSpec(true_peak_db=-2.0, target_lufs=-16.0)
        self.assertTrue(any("clipping" in i for i in mix.check_loudness(-15.0, -0.5)))
        self.assertEqual(mix.check_loudness(-16.0, -3.0), [])

    def test_subtitle_duration_non_positive_detected(self):
        subs = [SubtitleCue(cue_id="S1", start_sec=1.0, end_sec=1.0, text="x", speaker="A")]
        issues = audio_gate([AudioTrack(track_id="T1", kind=TrackKind.DIALOGUE, media_asset_id="id")], subs)
        self.assertTrue(any("non-positive duration" in i for i in issues))


class AdoptionTests(unittest.TestCase):
    def _adoption(self, count: int = 3) -> ShotAdoption:
        return ShotAdoption(
            shot_id="SHOT_001_001_001",
            candidates=[
                ShotCandidate(candidate_id=f"K{i}", kind=CandidateKind.KEYFRAME)
                for i in range(count)
            ],
        )

    def test_adopt_supersedes_previous(self):
        adoption = self._adoption()
        adoption.adopt("K0")
        adoption.adopt("K1")
        self.assertEqual(adoption.current(CandidateKind.KEYFRAME).candidate_id, "K1")
        self.assertEqual(
            [c.candidate_id for c in adoption.candidates if c.status == CandidateStatus.SUPERSEDED],
            ["K0"],
        )
        self.assertEqual(assert_adoption_invariants(adoption), [])

    def test_locked_candidate_cannot_be_re_adopted(self):
        adoption = self._adoption()
        adoption.adopt("K0")
        adoption.lock("K0")
        with self.assertRaises(InvariantViolation):
            adoption.adopt("K1")

    def test_reject_locked_raises(self):
        adoption = self._adoption()
        adoption.adopt("K0")
        adoption.lock("K0")
        with self.assertRaises(InvariantViolation):
            adoption.reject("K0")

    def test_lock_before_adopt_raises(self):
        adoption = self._adoption()
        with self.assertRaises(InvariantViolation):
            adoption.lock("K0")


class AutoQCTests(unittest.TestCase):
    def test_missing_evidence_fails_closed(self):
        result = run_auto_qc(MediaEvidence(asset_id="A1"))
        self.assertTrue(result.has_gate_failures)

    def test_black_frame_detected(self):
        evidence = MediaEvidence(
            asset_id="A2",
            ffprobe={"duration_sec": 5.0},
            decoded_fully=True,
            frame_luma_sequence=[0.001] * 100,
        )
        result = run_auto_qc(evidence)
        self.assertTrue(any(f.code == "black-frame" and f.severity == "fail" for f in result.findings))

    def test_silent_audio_detected(self):
        evidence = MediaEvidence(
            asset_id="A3",
            ffprobe={"duration_sec": 5.0},
            decoded_fully=True,
            audio_rms_sequence=[0.0] * 10,
        )
        result = run_auto_qc(evidence)
        self.assertTrue(any(f.code == "silent-audio" for f in result.findings))

    def test_clean_media_passes_gate(self):
        luma = [0.3 + (i % 10) * 0.02 for i in range(60)]  # 正常亮度且有变化
        rms = [0.1 + (i % 5) * 0.05 for i in range(20)]
        evidence = MediaEvidence(
            asset_id="A4",
            ffprobe={"duration_sec": 5.0},
            decoded_fully=True,
            duration_contract_sec=5.0,
            frame_luma_sequence=luma,
            audio_rms_sequence=rms,
        )
        result = run_auto_qc(evidence)
        self.assertFalse(result.has_gate_failures)
        # 视觉类检查显式 unknown，不伪造结论
        self.assertIn("visual-face-region", [f.code for f in result.findings])

    def test_av_offset_warns(self):
        evidence = MediaEvidence(
            asset_id="A5",
            ffprobe={"duration_sec": 15.0},
            decoded_fully=True,
            audio_duration_sec=14.5,
        )
        result = run_auto_qc(evidence)
        self.assertTrue(any(f.code == "av-offset" for f in result.findings))

    def test_manual_decision_enum_values(self):
        self.assertEqual(
            [d.value for d in ManualDecision],
            ["Pass", "Retake", "Regenerate", "Patch in edit", "Blocked"],
        )


if __name__ == "__main__":
    unittest.main()