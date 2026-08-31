import json
import queue
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

import key_mouse_marco_weaver


class MacroFormatTests(unittest.TestCase):
    def test_repeat_settings_accept_arbitrary_counts_and_loop_mode(self):
        self.assertEqual(key_mouse_marco_weaver.parse_repeat_settings(key_mouse_marco_weaver.REPEAT_MODE_COUNT, "100000000000000000000"), 100000000000000000000)
        self.assertIsNone(key_mouse_marco_weaver.parse_repeat_settings(key_mouse_marco_weaver.REPEAT_MODE_LOOP, ""))
        with self.assertRaisesRegex(ValueError, "重复次数"):
            key_mouse_marco_weaver.parse_repeat_settings(key_mouse_marco_weaver.REPEAT_MODE_COUNT, "0")
        with self.assertRaisesRegex(ValueError, "重复模式"):
            key_mouse_marco_weaver.parse_repeat_settings("other", "1")

    def test_composition_speed_accepts_supported_multipliers(self):
        self.assertEqual(key_mouse_marco_weaver.parse_composition_speed("2"), 2.0)
        self.assertEqual(key_mouse_marco_weaver.parse_composition_speed(key_mouse_marco_weaver.SPEED_MIN), key_mouse_marco_weaver.SPEED_MIN)
        self.assertEqual(key_mouse_marco_weaver.parse_composition_speed(key_mouse_marco_weaver.SPEED_MAX), key_mouse_marco_weaver.SPEED_MAX)
        for value in ("", "fast", 0, key_mouse_marco_weaver.SPEED_MAX + 0.01, True):
            with self.assertRaisesRegex(ValueError, "编排速度"):
                key_mouse_marco_weaver.parse_composition_speed(value)

    def test_loop_progress_text_is_unbounded(self):
        self.assertEqual(
            key_mouse_marco_weaver.MacroApp._format_playback_progress(0.5, 1.0, 1.0, loop_index=4, repeat=None),
            "第 5 次 · 循环 · 0.50s / 1.00s",
        )

    def test_loop_worker_stops_only_after_stop_event(self):
        app = key_mouse_marco_weaver.MacroApp.__new__(key_mouse_marco_weaver.MacroApp)
        app.stop_playback = threading.Event()
        app.pause_playback = threading.Event()
        app.ui_queue = queue.Queue()
        app._queue_log = mock.Mock()
        app._release_inputs = mock.Mock(return_value=[])
        calls = []

        def replay_once(*_args, **_kwargs):
            calls.append(1)
            if len(calls) == 3:
                app.stop_playback.set()

        app._replay_event = replay_once
        app._play_worker([{"kind": "key", "t": 0, "vk": 65, "action": "down"}], 1.0, None, None)
        self.assertEqual(len(calls), 3)
        updates = []
        while not app.ui_queue.empty():
            kind, value = app.ui_queue.get()
            if kind == "playback_done":
                updates.append(value)
        self.assertEqual(updates, ["已停止"])

    def test_hook_source_timestamp_handles_32_bit_rollover(self):
        start_tick = (7 << 32) + 0xFFFFFFF0
        reference_tick = (8 << 32) + 0x20
        source_tick = 0x10
        self.assertEqual(
            key_mouse_marco_weaver.expand_hook_tick(source_tick, reference_tick),
            (8 << 32) + source_tick,
        )
        self.assertAlmostEqual(
            key_mouse_marco_weaver.hook_elapsed_seconds(source_tick, start_tick, reference_tick),
            0.032,
        )

    def test_recording_hook_uses_one_thread_for_keyboard_and_mouse(self):
        hook = key_mouse_marco_weaver.RecordingHookThread(mock.Mock(), mock.Mock(), mock.Mock())
        self.assertEqual(
            set(hook.handlers),
            {key_mouse_marco_weaver.WH_KEYBOARD_LL, key_mouse_marco_weaver.WH_MOUSE_LL},
        )
        self.assertEqual(set(hook.procs), set(hook.handlers))

    def test_recording_stop_sorts_source_times_and_preserves_equal_time_order(self):
        app = key_mouse_marco_weaver.MacroApp.__new__(key_mouse_marco_weaver.MacroApp)
        app.recording = True
        app.events_lock = threading.Lock()
        app.events = [
            {"kind": "key", "t": 0.010, "vk": 65, "action": "down"},
            {"kind": "mouse", "t": 0.009, "message": key_mouse_marco_weaver.WM_LBUTTONDOWN},
            {"kind": "key", "t": 0.010, "vk": 66, "action": "down"},
        ]
        app._stop_recording_hooks = mock.Mock()
        app.record_button = mock.Mock()
        app.status_var = mock.Mock()
        app.log = mock.Mock()
        app.update_count = mock.Mock()
        app.stop_recording()
        self.assertEqual(
            [(event["kind"], event.get("vk")) for event in app.events],
            [("mouse", None), ("key", 65), ("key", 66)],
        )

    def test_events_are_normalized_and_monotonic(self):
        events = key_mouse_marco_weaver.validate_events([
            {"kind": "key", "t": 1, "vk": 65, "action": "down"},
            {"kind": "key", "t": 0.5, "vk": 65, "action": "up"},
        ])
        self.assertEqual(events[1]["t"], 1.0)
        self.assertEqual(events[0]["scan"], 0)

    def test_move_event_keeps_slots_ordered_and_preserves_time_gaps(self):
        events = [
            {"kind": "key", "t": 0.0, "vk": 65, "action": "down"},
            {"kind": "key", "t": 1.0, "vk": 66, "action": "down"},
            {"kind": "key", "t": 3.0, "vk": 67, "action": "down"},
        ]
        moved = key_mouse_marco_weaver.move_event(events, 1, -1)
        self.assertEqual([event["vk"] for event in moved], [66, 65, 67])
        self.assertEqual([event["t"] for event in moved], [0.0, 1.0, 3.0])

    def test_move_event_to_supports_dragging_across_multiple_slots(self):
        events = [
            {"kind": "key", "t": 0.0, "vk": 65, "action": "down"},
            {"kind": "key", "t": 0.5, "vk": 66, "action": "down"},
            {"kind": "key", "t": 2.0, "vk": 67, "action": "down"},
            {"kind": "key", "t": 4.0, "vk": 68, "action": "down"},
        ]
        moved = key_mouse_marco_weaver.move_event_to(events, 0, 3)
        self.assertEqual([event["vk"] for event in moved], [66, 67, 68, 65])
        self.assertEqual([event["t"] for event in moved], [0.0, 0.5, 2.0, 4.0])
        self.assertEqual([event["vk"] for event in events], [65, 66, 67, 68])

    def test_move_list_item_supports_composer_drag_order(self):
        items = [{"name": "first"}, {"name": "second"}, {"name": "third"}]
        reordered = key_mouse_marco_weaver.move_list_item(items, 2, 0)
        self.assertEqual([item["name"] for item in reordered], ["third", "first", "second"])
        self.assertEqual([item["name"] for item in items], ["first", "second", "third"])

    def test_invalid_event_is_rejected(self):
        with self.assertRaises(ValueError):
            key_mouse_marco_weaver.validate_events([{"kind": "unknown", "t": 0}])

    def test_non_finite_timestamps_and_delay_events_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "timestamp"):
            key_mouse_marco_weaver.validate_events([{"kind": "key", "t": "nan", "vk": 65}])
        with self.assertRaisesRegex(ValueError, "unknown kind"):
            key_mouse_marco_weaver.validate_events([{"kind": "delay", "t": 0, "duration": "inf"}])
        with self.assertRaises(ValueError):
            key_mouse_marco_weaver.build_action_events("空白", base_time=1.0)

    def test_event_code_ranges_and_boolean_values_are_normalized_strictly(self):
        normalized = key_mouse_marco_weaver.validate_events([
            {"kind": "key", "t": 0, "vk": 65, "scan": 30, "action": "down", "extended": "false"},
        ])
        self.assertFalse(normalized[0]["extended"])
        with self.assertRaises(ValueError):
            key_mouse_marco_weaver.validate_events([{"kind": "key", "t": 0, "vk": 65, "scan": -1}])
        with self.assertRaises(ValueError):
            key_mouse_marco_weaver.validate_events([{"kind": "key", "t": 0, "vk": 65, "extended": "maybe"}])
        with self.assertRaises(ValueError):
            key_mouse_marco_weaver.validate_events([{"kind": "mouse", "t": 0, "message": 999}])
        with self.assertRaises(ValueError):
            key_mouse_marco_weaver.validate_events([{"kind": "mouse", "t": 0, "x": 1 << 40}])

    def test_delay_module_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown kind"):
            key_mouse_marco_weaver.validate_events([{"kind": "delay", "t": 0.25, "duration": "0.25"}])

    def test_key_display_names_hide_codes_from_user(self):
        self.assertEqual(key_mouse_marco_weaver.key_display_name(65), "A")
        self.assertEqual(key_mouse_marco_weaver.key_display_name(46), "Delete")
        self.assertEqual(key_mouse_marco_weaver.key_display_name(8), "Backspace")
        self.assertEqual(key_mouse_marco_weaver.key_display_name(46, "Delete"), "Delete")
        self.assertEqual(key_mouse_marco_weaver.key_display_name(8, "BackSpace"), "Backspace")
        self.assertEqual(key_mouse_marco_weaver.key_display_name(65, "??"), "A")
        self.assertEqual(key_mouse_marco_weaver.key_display_name(65, "a"), "A")
        self.assertEqual(key_mouse_marco_weaver.tk_event_vk(SimpleNamespace(keycode=229, char="a", keysym_num=97)), 65)

    def test_timeline_key_details_include_name_and_codes(self):
        app = key_mouse_marco_weaver.MacroApp.__new__(key_mouse_marco_weaver.MacroApp)
        self.assertEqual(
            app._event_label({"kind": "key", "vk": 77, "scan": 50, "action": "down"}),
            "M (VK 77 / scan 50) down",
        )

    def test_mouse_action_labels_include_wheel_direction_and_side_button(self):
        up = (120 & 0xFFFF) << 16
        down = ((-120) & 0xFFFF) << 16
        self.assertEqual(key_mouse_marco_weaver.mouse_action_label(key_mouse_marco_weaver.WM_MOUSEWHEEL, up), "垂直滚轮向上")
        self.assertEqual(key_mouse_marco_weaver.mouse_action_label(key_mouse_marco_weaver.WM_MOUSEWHEEL, down), "垂直滚轮向下")
        self.assertEqual(key_mouse_marco_weaver.mouse_action_label(key_mouse_marco_weaver.WM_MOUSEHWHEEL, up), "水平滚轮向右")
        self.assertEqual(key_mouse_marco_weaver.mouse_action_label(key_mouse_marco_weaver.WM_MOUSEHWHEEL, down), "水平滚轮向左")
        self.assertEqual(key_mouse_marco_weaver.mouse_action_label(key_mouse_marco_weaver.WM_XBUTTONDOWN, 1 << 16), "侧键1按下")
        self.assertEqual(key_mouse_marco_weaver.mouse_action_label(key_mouse_marco_weaver.WM_XBUTTONUP, 2 << 16), "侧键2释放")
        self.assertEqual(key_mouse_marco_weaver.wheel_amount(down), 120)

    def test_shared_action_builder_supports_insert_and_property_modes(self):
        inserted_down = key_mouse_marco_weaver.build_action_events("左键按下", base_time=1.0, x=10, y=20)
        self.assertEqual(len(inserted_down), 1)
        self.assertEqual(inserted_down[0]["message"], key_mouse_marco_weaver.WM_LBUTTONDOWN)

        edited_wheel = key_mouse_marco_weaver.build_action_events("垂直滚轮向下", base_time=2.0, delta=240)
        self.assertEqual(len(edited_wheel), 1)
        self.assertEqual(edited_wheel[0]["t"], 2.0)
        self.assertEqual(key_mouse_marco_weaver.signed_word(key_mouse_marco_weaver.high_word(edited_wheel[0]["data"])), -240)

        with self.assertRaises(ValueError):
            key_mouse_marco_weaver.build_action_events("左键点击", base_time=1.0)

    def test_action_catalog_is_single_step_and_shared(self):
        labels = [entry["label"] for entry in key_mouse_marco_weaver.ACTION_CATALOG]
        self.assertEqual(list(key_mouse_marco_weaver.EVENT_TYPE_VALUES), ["鼠标", "键盘"])
        self.assertEqual(
            [entry["label"] for entry in key_mouse_marco_weaver.ACTION_CATALOG if entry["kind"] == "mouse"],
            list(key_mouse_marco_weaver.MOUSE_ACTION_VALUES),
        )
        self.assertEqual(key_mouse_marco_weaver.ACTION_TYPE_VALUES["键盘"], ("按下", "释放"))
        self.assertEqual(key_mouse_marco_weaver.ACTION_TYPE_VALUES["鼠标"], key_mouse_marco_weaver.MOUSE_ACTION_VALUES)
        self.assertFalse(any("点击" in label for label in labels))
        for label in labels:
            if label != "键盘按键":
                created = key_mouse_marco_weaver.build_action_events(label, base_time=1.0, x=10, y=20, delta=120)
                self.assertEqual(len(created), 1, label)

    def test_event_category_and_action_type_labels(self):
        key = {"kind": "key", "action": "up"}
        mouse = {"kind": "mouse", "message": key_mouse_marco_weaver.WM_MOUSEWHEEL, "data": ((120 & 0xFFFF) << 16)}
        self.assertEqual(key_mouse_marco_weaver.event_category_for_event(key), "键盘")
        self.assertEqual(key_mouse_marco_weaver.action_type_for_event(key), "释放")
        self.assertEqual(key_mouse_marco_weaver.event_category_for_event(mouse), "鼠标")
        self.assertEqual(key_mouse_marco_weaver.action_type_for_event(mouse), "垂直滚轮向上")
        self.assertEqual(
            key_mouse_marco_weaver.action_type_for_event({"kind": "mouse", "message": key_mouse_marco_weaver.WM_MOUSEMOVE}),
            "移动",
        )
        with self.assertRaisesRegex(ValueError, "错误的事件类型"):
            key_mouse_marco_weaver.validate_editor_choice("空白", "空白")

    def test_editor_choices_accept_text_and_reject_unknown_values(self):
        self.assertEqual(key_mouse_marco_weaver.validate_editor_choice(" 鼠标 ", " 左键按下 "), ("鼠标", "左键按下"))
        self.assertEqual(key_mouse_marco_weaver.validate_editor_choice("键盘", "释放"), ("键盘", "释放"))
        with self.assertRaisesRegex(ValueError, "错误的事件类型"):
            key_mouse_marco_weaver.validate_editor_choice("视频", "按下")
        with self.assertRaisesRegex(ValueError, "错误的动作"):
            key_mouse_marco_weaver.validate_editor_choice("鼠标", "点击")

    def test_timeline_gaps_represent_waits(self):
        events = key_mouse_marco_weaver.validate_events([
            {"kind": "key", "t": 1.0, "vk": 65, "action": "down"},
            {"kind": "mouse", "t": 3.5, "message": key_mouse_marco_weaver.WM_MOUSEMOVE, "x": 0, "y": 0},
        ])
        self.assertEqual(key_mouse_marco_weaver.event_end_time(events[0]), 1.0)
        self.assertEqual(max(key_mouse_marco_weaver.event_end_time(event) for event in events), 3.5)

    def test_playback_wait_emits_continuous_progress(self):
        app = key_mouse_marco_weaver.MacroApp.__new__(key_mouse_marco_weaver.MacroApp)
        app.stop_playback = threading.Event()
        app.pause_playback = threading.Event()
        app.ui_queue = queue.Queue()
        app._wait_until(0.12, progress_start=0.0, progress_end=1.0, total_time=1.0)
        updates = []
        while not app.ui_queue.empty():
            kind, value = app.ui_queue.get()
            if kind == "progress":
                updates.append(value)
        self.assertGreaterEqual(len(updates), 2)
        self.assertIn("1.00s / 1.00s", updates[-1])

    def test_release_inputs_retries_and_keeps_only_failed_states(self):
        app = key_mouse_marco_weaver.MacroApp.__new__(key_mouse_marco_weaver.MacroApp)
        active_keys = {(65, 30, False)}
        active_buttons = {(key_mouse_marco_weaver.MOUSEEVENTF_LEFTUP, 0)}
        with (
            mock.patch.object(
                key_mouse_marco_weaver,
                "send_keyboard",
                side_effect=[OSError("temporary"), None],
            ) as send_keyboard,
            mock.patch.object(
                key_mouse_marco_weaver,
                "send_mouse",
                side_effect=OSError("blocked"),
            ) as send_mouse,
            mock.patch.object(key_mouse_marco_weaver.time, "sleep"),
        ):
            failures = app._release_inputs(active_keys, active_buttons)
        self.assertEqual(send_keyboard.call_count, 2)
        self.assertEqual(send_mouse.call_count, key_mouse_marco_weaver.INPUT_RELEASE_ATTEMPTS)
        self.assertEqual(active_keys, set())
        self.assertEqual(active_buttons, {(key_mouse_marco_weaver.MOUSEEVENTF_LEFTUP, 0)})
        self.assertEqual(failures, ["鼠标左键：blocked"])

    def test_playback_queues_warning_when_release_still_fails(self):
        app = key_mouse_marco_weaver.MacroApp.__new__(key_mouse_marco_weaver.MacroApp)
        app.stop_playback = threading.Event()
        app.pause_playback = threading.Event()
        app.ui_queue = queue.Queue()
        app._queue_log = mock.Mock()
        app._replay_event = mock.Mock()
        app._release_inputs = mock.Mock(return_value=["按键 A：blocked"])
        app._play_worker(
            [{"kind": "key", "t": 0, "vk": 65, "action": "down"}],
            1.0,
            1,
            None,
        )
        queued = []
        while not app.ui_queue.empty():
            queued.append(app.ui_queue.get())
        warning = next(value for kind, value in queued if kind == "release_warning")
        status = next(value for kind, value in queued if kind == "playback_done")
        self.assertIn("已分别重试 3 次", warning)
        self.assertIn("按键 A：blocked", warning)
        self.assertEqual(status, "回放完成 · 输入释放失败")

    def test_undo_restores_events_and_selection(self):
        app = key_mouse_marco_weaver.MacroApp.__new__(key_mouse_marco_weaver.MacroApp)
        app.events = [{"kind": "key", "t": 0.1, "vk": 65, "scan": 30, "action": "down"}]
        app.events_lock = threading.Lock()
        app.selected_index = 0
        app.selected_indices = [0]
        app.current_file = None
        app.loaded_screen = None
        app.recording = False
        app.playing = False
        app.undo_stack = []
        app.file_var = mock.Mock()
        app.status_var = mock.Mock()
        app._refresh_tree = mock.Mock()
        app.update_count = mock.Mock()
        app.log = mock.Mock()
        app._push_undo()
        app.events[0]["vk"] = 66
        app.selected_index = None
        app.selected_indices = []
        app.undo()
        self.assertEqual(app.events[0]["vk"], 65)
        self.assertEqual(app.selected_index, 0)
        self.assertEqual(app.selected_indices, [0])

    def test_redo_restores_undone_edit(self):
        app = key_mouse_marco_weaver.MacroApp.__new__(key_mouse_marco_weaver.MacroApp)
        app.events = [{"kind": "key", "t": 0.1, "vk": 65, "scan": 30, "action": "down"}]
        app.events_lock = threading.Lock()
        app.selected_index = 0
        app.selected_indices = [0]
        app.current_file = None
        app.loaded_screen = None
        app.recording = False
        app.playing = False
        app.undo_stack = []
        app.redo_stack = []
        app.file_var = mock.Mock()
        app.status_var = mock.Mock()
        app._refresh_tree = mock.Mock()
        app.update_count = mock.Mock()
        app.log = mock.Mock()
        app._push_undo()
        app.events[0]["vk"] = 66
        app.undo()
        app.redo()
        self.assertEqual(app.events[0]["vk"], 66)
        self.assertEqual(app.selected_index, 0)

    def test_new_edit_clears_redo_history(self):
        app = key_mouse_marco_weaver.MacroApp.__new__(key_mouse_marco_weaver.MacroApp)
        app.events = []
        app.events_lock = threading.Lock()
        app.selected_index = None
        app.selected_indices = []
        app.current_file = None
        app.loaded_screen = None
        app.recording = False
        app.playing = False
        app.undo_stack = []
        app.redo_stack = []
        app.file_var = mock.Mock()
        app.status_var = mock.Mock()
        app._refresh_tree = mock.Mock()
        app.update_count = mock.Mock()
        app.log = mock.Mock()
        app._push_undo()
        app.events.append({"kind": "key", "t": 0, "vk": 65, "action": "down"})
        app.undo()
        app._push_undo()
        self.assertEqual(app.redo_stack, [])

    def test_undo_stack_is_limited_to_twenty_entries(self):
        app = key_mouse_marco_weaver.MacroApp.__new__(key_mouse_marco_weaver.MacroApp)
        app.events = []
        app.events_lock = threading.Lock()
        app.selected_index = None
        app.selected_indices = []
        app.current_file = None
        app.loaded_screen = None
        app.recording = False
        app.playing = False
        app.undo_stack = []
        for _ in range(25):
            app._push_undo()
        self.assertEqual(len(app.undo_stack), 20)

    def test_undo_restores_inspector_values(self):
        class Variable:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        app = key_mouse_marco_weaver.MacroApp.__new__(key_mouse_marco_weaver.MacroApp)
        app.events = [{"kind": "key", "t": 0, "vk": 65, "scan": 30, "action": "down"}]
        app.events_lock = threading.Lock()
        app.selected_index = 0
        app.selected_indices = [0]
        app.current_file = None
        app.loaded_screen = None
        app.recording = False
        app.playing = False
        app.undo_stack = []
        app._pending_key_undo = None
        app.property_vars = {"key": Variable("A")}
        app.property_extended = Variable(False)
        app.advanced_key_var = Variable(False)
        app.file_var = mock.Mock()
        app.status_var = mock.Mock()
        app._refresh_tree = mock.Mock()
        app.update_count = mock.Mock()
        app.log = mock.Mock()
        app._push_undo()
        app.property_vars["key"].set("B")
        app.events[0]["vk"] = 66
        app.undo()
        self.assertEqual(app.events[0]["vk"], 65)
        self.assertEqual(app.property_vars["key"].get(), "A")
        app.redo()
        self.assertEqual(app.events[0]["vk"], 66)
        self.assertEqual(app.property_vars["key"].get(), "B")

    def test_atomic_payload_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "macro.json"
            key_mouse_marco_weaver.save_macro_payload(path, [{"kind": "mouse", "t": 0, "message": key_mouse_marco_weaver.WM_MOUSEMOVE, "x": 2, "y": 3, "data": 0}])
            payload, events = key_mouse_marco_weaver.load_macro_payload(path)
            self.assertEqual(payload["schema_version"], 2)
            self.assertEqual(events[0]["x"], 2)
            self.assertNotIn(".tmp", "".join(p.name for p in path.parent.iterdir()))

    def test_atomic_payload_cleans_temporary_file_when_replace_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "macro.json"
            with mock.patch.object(key_mouse_marco_weaver.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    key_mouse_marco_weaver.save_macro_payload(
                        path,
                        [{"kind": "key", "t": 0, "vk": 65, "action": "down"}],
                        screen_bounds={"left": 0, "top": 0, "width": 1, "height": 1},
                    )
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_macro_load_requires_current_schema_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "macro.json"
            path.write_text(json.dumps({"events": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                key_mouse_marco_weaver.load_macro_payload(path)
            path.write_text(json.dumps({"schema_version": 1, "events": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                key_mouse_marco_weaver.load_macro_payload(path)

    def test_compose_macro_events_preserves_sequence_repeats_and_gaps(self):
        first = [
            {"kind": "key", "t": 0.2, "vk": 65, "action": "down"},
            {"kind": "key", "t": 1.0, "vk": 65, "action": "up"},
        ]
        second = [
            {"kind": "key", "t": 0.0, "vk": 66, "action": "down"},
            {"kind": "key", "t": 0.5, "vk": 66, "action": "up"},
        ]
        composed = key_mouse_marco_weaver.compose_macro_events([
            {"events": first, "repeat": 1},
            {"events": second, "repeat": 2},
            {"events": first, "repeat": 3},
        ])
        self.assertEqual(
            [(event["vk"], event["action"]) for event in composed],
            [
                (65, "down"), (65, "up"),
                (66, "down"), (66, "up"),
                (66, "down"), (66, "up"),
                (65, "down"), (65, "up"),
                (65, "down"), (65, "up"),
                (65, "down"), (65, "up"),
            ],
        )
        self.assertEqual(
            [event["t"] for event in composed],
            [0.2, 1.0, 1.0, 1.5, 1.5, 2.0, 2.2, 3.0, 3.2, 4.0, 4.2, 5.0],
        )
        self.assertEqual(first[0]["t"], 0.2)
        self.assertEqual(second[-1]["t"], 0.5)

    def test_compose_macro_events_applies_each_item_playback_speed(self):
        first = [
            {"kind": "key", "t": 0.2, "vk": 65, "action": "down"},
            {"kind": "key", "t": 1.0, "vk": 65, "action": "up"},
        ]
        second = [
            {"kind": "key", "t": 0.0, "vk": 66, "action": "down"},
            {"kind": "key", "t": 0.5, "vk": 66, "action": "up"},
        ]
        composed = key_mouse_marco_weaver.compose_macro_events([
            {"events": first, "speed": 2, "repeat": 2},
            {"events": second, "speed": 0.5, "repeat": 1},
        ])
        self.assertEqual(
            [event["t"] for event in composed],
            [0.1, 0.5, 0.6, 1.0, 1.0, 2.0],
        )

    def test_compose_macro_events_rejects_invalid_item_speed(self):
        with self.assertRaisesRegex(ValueError, "第 1 个宏编排速度"):
            key_mouse_marco_weaver.compose_macro_events([
                {"events": [{"kind": "key", "t": 0, "vk": 65}], "speed": 0}
            ])

    def test_compose_macro_events_maps_each_source_screen_to_output_screen(self):
        composed = key_mouse_marco_weaver.compose_macro_events(
            [{
                "events": [{"kind": "mouse", "t": 0, "message": key_mouse_marco_weaver.WM_MOUSEMOVE, "x": 99, "y": 49}],
                "repeat": 1,
                "screen": {"left": 0, "top": 0, "width": 100, "height": 50},
            }],
            target_screen={"left": -200, "top": -100, "width": 200, "height": 100},
        )
        self.assertEqual((composed[0]["x"], composed[0]["y"]), (-1, -1))

    def test_compose_macro_events_rejects_empty_items_and_invalid_repeats(self):
        with self.assertRaisesRegex(ValueError, "至少需要一个宏"):
            key_mouse_marco_weaver.compose_macro_events([])
        with self.assertRaisesRegex(ValueError, "没有可编排事件"):
            key_mouse_marco_weaver.compose_macro_events([{"events": [], "repeat": 1}])
        with self.assertRaisesRegex(ValueError, "编排次数"):
            key_mouse_marco_weaver.compose_macro_events([
                {"events": [{"kind": "key", "t": 0, "vk": 65}], "repeat": 0}
            ])

    def test_composed_payload_round_trip_leaves_source_files_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            output = Path(directory) / "composed.json"
            key_mouse_marco_weaver.save_macro_payload(
                source,
                [{"kind": "key", "t": 0.25, "vk": 65, "action": "down"}],
                screen_bounds={"left": 0, "top": 0, "width": 1920, "height": 1080},
            )
            source_before = source.read_bytes()
            payload, events = key_mouse_marco_weaver.load_macro_payload(source)
            composed = key_mouse_marco_weaver.compose_macro_events([
                {"events": events, "repeat": 3, "screen": payload["screen"]}
            ])
            key_mouse_marco_weaver.save_macro_payload(output, composed, screen_bounds=payload["screen"])
            _output_payload, output_events = key_mouse_marco_weaver.load_macro_payload(output)
            self.assertEqual(source.read_bytes(), source_before)
            self.assertEqual(len(output_events), 3)
            self.assertEqual([event["t"] for event in output_events], [0.25, 0.5, 0.75])

    def test_screen_point_mapping(self):
        source = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        with mock.patch.object(key_mouse_marco_weaver, "virtual_screen_bounds", return_value=source):
            mapped = key_mouse_marco_weaver.map_screen_point(960, 540, source)
        self.assertEqual(mapped, (960, 540))

    def test_clamp_screen_point_keeps_negative_hook_coordinates_reachable(self):
        bounds = {"left": 0, "top": 0, "width": 1707, "height": 1067}
        self.assertEqual(key_mouse_marco_weaver.clamp_screen_point(1525, -6, bounds), (1525, 0))
        self.assertEqual(key_mouse_marco_weaver.clamp_screen_point(-20, 1200, bounds), (0, 1066))

    def test_clamp_screen_point_preserves_negative_secondary_monitor_coordinates(self):
        bounds = {"left": -1920, "top": -200, "width": 3620, "height": 1280}
        self.assertEqual(key_mouse_marco_weaver.clamp_screen_point(-100, -50, bounds), (-100, -50))

    def test_delete_mouse_moves_removes_every_move_and_preserves_actions(self):
        events = [
            {"kind": "mouse", "t": 0.0, "message": key_mouse_marco_weaver.WM_MOUSEMOVE, "x": 1, "y": 1},
            {"kind": "mouse", "t": 0.1, "message": key_mouse_marco_weaver.WM_MOUSEMOVE, "x": 2, "y": 2},
            {"kind": "mouse", "t": 0.2, "message": key_mouse_marco_weaver.WM_MOUSEMOVE, "x": 3, "y": 3},
            {"kind": "key", "t": 0.3, "vk": 65, "action": "down"},
            {"kind": "mouse", "t": 0.4, "message": key_mouse_marco_weaver.WM_MOUSEMOVE, "x": 4, "y": 4},
            {"kind": "mouse", "t": 0.5, "message": key_mouse_marco_weaver.WM_MOUSEMOVE, "x": 5, "y": 5},
            {"kind": "mouse", "t": 0.6, "message": key_mouse_marco_weaver.WM_MOUSEMOVE, "x": 6, "y": 6},
            {"kind": "mouse", "t": 0.7, "message": key_mouse_marco_weaver.WM_LBUTTONDOWN, "x": 7, "y": 7},
            {"kind": "mouse", "t": 0.8, "message": key_mouse_marco_weaver.WM_MOUSEMOVE, "x": 8, "y": 8},
            {"kind": "mouse", "t": 0.9, "message": key_mouse_marco_weaver.WM_MOUSEMOVE, "x": 9, "y": 9},
        ]
        filtered = key_mouse_marco_weaver.delete_mouse_moves(events)
        self.assertFalse(any(event.get("message") == key_mouse_marco_weaver.WM_MOUSEMOVE for event in filtered))
        self.assertEqual([event["kind"] for event in filtered], ["key", "mouse"])
        self.assertEqual(filtered[1]["message"], key_mouse_marco_weaver.WM_LBUTTONDOWN)

    def test_delete_mouse_moves_preserves_non_move_events_and_timestamps(self):
        events = [
            {"kind": "mouse", "t": 0.1, "message": key_mouse_marco_weaver.WM_MOUSEMOVE, "x": 1, "y": 2},
            {"kind": "key", "t": 0.2, "vk": 65, "action": "down"},
            {"kind": "mouse", "t": 0.3, "message": key_mouse_marco_weaver.WM_MOUSEMOVE, "x": 3, "y": 4},
            {"kind": "mouse", "t": 0.4, "message": key_mouse_marco_weaver.WM_MOUSEMOVE, "x": 5, "y": 6},
        ]
        filtered = key_mouse_marco_weaver.delete_mouse_moves(events)
        self.assertEqual([event["kind"] for event in filtered], ["key"])
        self.assertEqual(filtered[0]["vk"], 65)
        self.assertEqual(filtered[0]["t"], 0.2)

    def test_delete_mouse_moves_removes_single_and_pure_move_traces(self):
        events = [
            {"kind": "mouse", "t": 0.1, "message": key_mouse_marco_weaver.WM_MOUSEMOVE, "x": 1, "y": 2},
            {"kind": "mouse", "t": 0.2, "message": key_mouse_marco_weaver.WM_MOUSEMOVE, "x": 3, "y": 4},
        ]
        self.assertEqual(key_mouse_marco_weaver.delete_mouse_moves(events), [])
        self.assertEqual(key_mouse_marco_weaver.delete_mouse_moves([events[0]]), [])
        self.assertEqual(key_mouse_marco_weaver.delete_mouse_moves([]), [])


if __name__ == "__main__":
    unittest.main()
