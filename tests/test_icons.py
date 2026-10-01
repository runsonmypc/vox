"""Tests for procedural tray icons."""

import io

import numpy as np
import pytest

from vox.ui.icons import ICON_SIZE, IconState, is_template, make_app_icon, make_icon


def _pixels(state):
    return np.asarray(make_icon(state)).reshape(-1, 4).astype(int)


def _has_color(px, rgb, tol=40):
    opaque = px[px[:, 3] > 200]
    return bool((np.abs(opaque[:, :3] - rgb) <= tol).all(axis=1).any())


@pytest.mark.parametrize("state", list(IconState))
def test_icon_is_rgba_square_with_visible_glyph(state):
    img = make_icon(state)
    assert img.mode == "RGBA"
    assert img.size == (ICON_SIZE, ICON_SIZE)
    assert img.getchannel("A").getextrema() == (0, 255)  # transparent background, opaque glyph


@pytest.mark.parametrize("state", [IconState.IDLE, IconState.PAUSED])
def test_inactive_icons_are_monochrome_templates(state):
    px = _pixels(state)
    visible = px[px[:, 3] > 0]
    assert is_template(state)
    assert (visible[:, 0] == visible[:, 1]).all() and (visible[:, 1] == visible[:, 2]).all()


def test_recording_is_red_and_not_template():
    assert not is_template(IconState.RECORDING)
    assert _has_color(_pixels(IconState.RECORDING), (255, 59, 48))


@pytest.mark.parametrize("state", [IconState.RECORDING, IconState.PROCESSING])
def test_active_icons_light_the_grille_on_a_neutral_body(state):
    """The body must read on light and dark menu bars, since macOS does not tint colored icons."""
    assert _has_color(_pixels(state), (142, 142, 147), tol=8)


def test_idle_icon_has_grille_slots_cut_out():
    alpha = np.asarray(make_icon(IconState.IDLE, size=176))[..., 3]
    column = alpha[:, 88]  # down the capsule's center
    capsule = column[int(1.6 * 8) + 4 : int(13 * 8) - 4]
    assert (capsule > 200).any() and (capsule < 20).any()


def test_processing_is_blue_and_not_template():
    assert not is_template(IconState.PROCESSING)
    assert _has_color(_pixels(IconState.PROCESSING), (80, 190, 255))


@pytest.mark.parametrize("state", [IconState.RECORDING, IconState.PROCESSING])
def test_active_icons_have_no_black_pixels(state):
    """Non-template icons are not tinted by macOS, so black would vanish on a dark menu bar."""
    px = _pixels(state)
    assert not ((px[:, 3] > 128) & (px[:, :3].max(axis=1) < 60)).any()


def test_states_are_visually_distinct():
    assert len({make_icon(s).tobytes() for s in IconState}) == len(IconState)


def test_icon_encodes_to_png_buffer():
    buf = io.BytesIO()
    make_icon(IconState.RECORDING, size=22).save(buf, "png")
    assert buf.getvalue().startswith(b"\x89PNG")


@pytest.mark.parametrize("state", [IconState.IDLE, IconState.PAUSED])
def test_light_variant_draws_inactive_glyph_light_for_dark_linux_panels(state):
    px = np.asarray(make_icon(state, light=True)).reshape(-1, 4).astype(int)
    opaque = px[px[:, 3] > 200]
    assert len(opaque) and (opaque[:, :3] >= 200).all()


@pytest.mark.parametrize("state", [IconState.RECORDING, IconState.PROCESSING])
def test_light_variant_keeps_active_icons(state):
    assert make_icon(state, light=True).tobytes() == make_icon(state).tobytes()


def test_app_icon_is_cog_microphone_on_gunmetal_rounded_square():
    img = make_app_icon(256)
    px = np.asarray(img).astype(int)
    assert img.mode == "RGBA" and img.size == (256, 256)
    assert px[0, 0, 3] == 0 and px[40, 40, 3] == 255  # rounded corner outside, plate inside
    assert (px[40, 40, :3] < 60).all()
    flat = px.reshape(-1, 4)
    assert _has_color(flat, (190, 150, 90), tol=12)  # brass cog
    assert _has_color(flat, (230, 221, 198), tol=12)  # bone capsule
    assert _has_color(flat, (255, 96, 76), tol=12)  # lit grille


@pytest.mark.parametrize('state,rgb', [(IconState.RECORDING, (255, 59, 48)),
                                    (IconState.PROCESSING, (80, 190, 255))])
def test_overlay_icon_has_transparent_plate_and_matches_tray_state(state, rgb):
    px = np.asarray(make_app_icon(128, with_plate=False, state=state)).astype(int)
    assert px[20, 20, 3] == 0
    assert _has_color(px.reshape(-1, 4), rgb, tol=8)
    assert _has_color(_pixels(state), rgb, tol=8)
