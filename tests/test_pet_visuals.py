import math
import unittest

from jarvis import pet


class PetVisualHelpersTest(unittest.TestCase):
    def test_canvas_matches_reference_image_proportion(self) -> None:
        self.assertEqual((pet.W, pet.H), (1206, 694))

    def test_runtime_orbits_keep_moving_in_idle(self) -> None:
        first = pet._runtime_orbit_angles(phase=0.0, state="idle")
        later = pet._runtime_orbit_angles(phase=1.0, state="idle")

        self.assertEqual(len(first), 3)
        self.assertEqual(len(later), 3)
        self.assertNotEqual(first, later)

    def test_active_states_move_orbits_faster_than_idle(self) -> None:
        idle_start = pet._runtime_orbit_angles(phase=0.0, state="idle")[0]
        idle_end = pet._runtime_orbit_angles(phase=1.0, state="idle")[0]
        thinking_start = pet._runtime_orbit_angles(phase=0.0, state="thinking")[0]
        thinking_end = pet._runtime_orbit_angles(phase=1.0, state="thinking")[0]

        idle_delta = abs(idle_end - idle_start)
        thinking_delta = abs(thinking_end - thinking_start)

        self.assertGreater(thinking_delta, idle_delta)

    def test_avatar_eye_breathes_only_while_thinking(self) -> None:
        dim = pet._avatar_eye_alpha("thinking", phase=0.0)
        bright = pet._avatar_eye_alpha("thinking", phase=math.pi / 4)
        idle_dim = pet._avatar_eye_alpha("idle", phase=0.0)
        idle_later = pet._avatar_eye_alpha("idle", phase=math.pi / 4)

        self.assertGreater(bright, dim)
        self.assertEqual(idle_dim, idle_later)
        self.assertLess(idle_dim, bright)


if __name__ == "__main__":
    unittest.main()
