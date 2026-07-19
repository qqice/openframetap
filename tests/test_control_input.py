from pathlib import Path

import pytest

from openframetap.app.input import (
    ControlInput,
    JoystickConfig,
    KeyboardInput,
    MockInput,
    TouchJoystickInput,
    map_touch_axes,
)


def test_input_normalization_and_json_configuration() -> None:
    config = JoystickConfig.load(Path("config/control-ui.json"))
    assert config.logical_width == 1280 and config.logical_height == 720
    assert config.deadzone == 0.12
    assert config.overlay_size == 340
    assert config.keyboard_output == 0.41
    assert (config.protocol_offset_min, config.protocol_offset_max) == (32, 188)
    with pytest.raises(ValueError):
        ControlInput(yaw=1.01)


def test_touch_coordinate_mapping_deadzone_curve_and_limit() -> None:
    config = JoystickConfig()
    assert map_touch_axes(config.center_x, config.center_y, config) == (0.0, 0.0)
    yaw, pitch = map_touch_axes(config.center_x + config.radius * 0.5, config.center_y, config)
    assert yaw == pytest.approx((0.5 - config.deadzone) / (1 - config.deadzone))
    assert pitch == pytest.approx(0.0)
    yaw, pitch = map_touch_axes(
        config.center_x + config.radius * 10,
        config.center_y - config.radius * 10,
        config,
    )
    assert (yaw**2 + pitch**2) ** 0.5 == pytest.approx(1.0)
    assert yaw > 0 and pitch > 0


def test_touch_single_finger_move_up_and_cancel_return_zero() -> None:
    source = TouchJoystickInput(JoystickConfig())
    active = source.touch_down("one", 260, 570)
    assert active.active and active.yaw > 0
    assert source.touch_down("two", 40, 570) == active
    moved = source.touch_move("one", 150, 460)
    assert moved.pitch > 0
    released = source.touch_up("one")
    assert not released.active and released.yaw == released.pitch == 0
    source.touch_down("one", 40, 570)
    cancelled = source.touch_cancel("one")
    assert not cancelled.active and cancelled.yaw == cancelled.pitch == 0


def test_keyboard_press_release_opposites_stop_and_exit() -> None:
    source = KeyboardInput(maximum_output=0.25)
    assert source.key_down("left").yaw == -0.25
    assert source.key_down("right").yaw == 0.0
    assert source.key_up("right").yaw == -0.25
    assert source.key_up("left").yaw == 0.0
    assert source.key_down("w").pitch == 0.25
    stopped = source.key_down("space")
    assert stopped.emergency_stop and not stopped.active
    exited = source.key_down("q")
    assert exited.exit_requested and not exited.active


def test_mock_input_clamps_and_releases() -> None:
    source = MockInput()
    active = source.set_axes(2, -2)
    assert active.yaw == 1 and active.pitch == -1 and active.active
    assert not source.release().active
