"""Mobile message layout and exceptions that must not fall between coach lists."""

from unittest import TestCase

from compcoach_live.whatsapp_schedule import EventPlan, PodAthlete, schedule_lines


def pool(name, main, side="", strip="A2"):
    return dict(name=name, main_coach=main, side_coach=side, source_strip=strip,
                pool_no="1", time_text="", pod="A")


class WhatsappScheduleTest(TestCase):
    def test_coach_scans_two_events_without_leaving_their_block(self):
        first = EventPlan("CADETS", [pool("First", "Rae", "Jay"), pool("Second", "Jay", "Rae")], {}, [])
        next_event = EventPlan("JUNIORS", [pool("Third", "Jay", "Jay")], {}, [])
        message = "\n".join(schedule_lines([first, next_event], ["Jay", "Rae", "Jay", "Idle"]))
        self.assertIn("🤺 *JAY*\n_CADETS_\n🔹 A2: Second [S: RAE] · Pool 1\n"
                      "🔸 A2: First [M: *RAE*] · Pool 1\n_JUNIORS_\n🔹 A2: Third · Pool 1\n\n🤺 *RAE*", message)
        self.assertEqual(message.count("🤺 *JAY*"), 1)
        self.assertEqual(message.count("🔸 Side coach"), 1)
        self.assertNotIn("IDLE", message)

    def test_shared_pod_does_not_grant_a_coach_the_entire_pod(self):
        event = EventPlan("CADETS", [pool("Baker", "Retired", "Rae"), pool("Kelly", "")], {
            "B": [PodAthlete("Taylor", ("Jay", "Rae"), " · T32"),
                  PodAthlete("Jordan", ("Jay",)), PodAthlete("Robin", ())]
        }, ["⚔ AFM vs AFM: Taylor vs Jordan · T32"])
        empty = EventPlan("JUNIORS", [], {}, [])
        message = "\n".join(schedule_lines([event, empty], ["Jay", "Rae"]))
        rae = message.split("🤺 RAE\n", 1)[1].split("\n\n", 1)[0]
        self.assertIn("_CADETS · Pools_\n🔸 A2: Baker [M: *RET*]", rae)
        self.assertIn("_CADETS · DE_\n◆ Pod B: Taylor · also assigned: Jay · T32", rae)
        self.assertNotIn("Jordan", rae)
        self.assertNotIn("Robin", rae)
        self.assertEqual(message.count("⚔ AFM vs AFM:"), 1)
        self.assertIn("⚠️ *NEEDS COACH / REASSIGNMENT*\n_CADETS · Pools_\n• A2 · Baker\n"
                      "• A2 · Kelly\n_CADETS · DE_\n• Pod B · Robin", message)
        self.assertIn("_JUNIORS_ · No active athletes yet.", message)

    def test_single_event_keeps_compact_pool_and_shared_pod_format(self):
        event = EventPlan("CADETS", [pool("Morgan", "Jo", "Pat")],
                          {"A": [PodAthlete("River", ("Jo", "Pat"))]}, [])
        message = "\n".join(schedule_lines([event], ["Jo", "Pat"]))
        self.assertIn("📍 *CADETS*", message)
        self.assertIn("🤺 PAT\n🔸 A2: Morgan [M: *JO*] · Pool 1", message)
        self.assertIn("*DIRECT ELIMINATION*\n\n◆ *POD A*\nCoaches: Jo / Pat\n• River", message)
        self.assertEqual(message.count("River"), 1)
        self.assertNotIn("_CADETS", message)
