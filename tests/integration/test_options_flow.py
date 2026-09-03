"""Options-flow tests: the form must accept every shape the frontend submits.

The label picker is the reason this file exists. A multi-select selector submits the
form *without* its key when the user clears it, voluptuous then fills in the schema
default and validates it, and ``LabelSelector(multiple=True)`` rejects anything that
is not a list — so a tuple default makes "remove my last label" fail with an unknown
error instead of saving.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.home_keeper_battery_notes.const import (
    DEFAULT_NAME_TEMPLATE,
    DOMAIN,
    OPT_LABELS,
    OPT_NAME_TEMPLATE,
)


async def _open_options(hass: HomeAssistant, options: dict | None = None) -> dict:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options=options or {})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "init"
    return result


async def _open_options_flow_id(
    hass: HomeAssistant, options: dict | None = None
) -> str:
    """The flow id of a freshly opened options form."""
    return (await _open_options(hass, options))["flow_id"]


async def test_options_form_prefills_the_current_labels(hass: HomeAssistant) -> None:
    """The picker shows what is configured, without that becoming the fallback."""
    result = await _open_options(hass, {OPT_LABELS: ["batterien"]})
    marker = next(key for key in result["data_schema"].schema if key == OPT_LABELS)
    assert marker.description == {"suggested_value": ["batterien"]}
    assert marker.default() == []


async def test_options_form_supplies_the_device_name_placeholder(
    hass: HomeAssistant,
) -> None:
    """The help text shows `{device_name}`, which ICU needs supplied as an argument.

    Without it the field renders as "Translation Error: The intl string context
    variable "device_name" was not provided". Escaping the braces with single quotes
    instead is rejected by hassfest.
    """
    result = await _open_options(hass)
    assert result["description_placeholders"] == {"device_name": "{device_name}"}


async def test_options_flow_saves_selected_labels(hass: HomeAssistant) -> None:
    flow_id = await _open_options_flow_id(hass)
    result = await hass.config_entries.options.async_configure(
        flow_id,
        user_input={
            OPT_NAME_TEMPLATE: DEFAULT_NAME_TEMPLATE,
            OPT_LABELS: ["batterien"],
        },
    )
    assert result["type"] == "create_entry"
    assert result["data"][OPT_LABELS] == ["batterien"]


async def test_options_flow_accepts_a_cleared_label_picker(hass: HomeAssistant) -> None:
    """A cleared picker omits the key entirely — the default must still validate."""
    flow_id = await _open_options_flow_id(hass, {OPT_LABELS: ["batterien"]})
    result = await hass.config_entries.options.async_configure(
        flow_id, user_input={OPT_NAME_TEMPLATE: DEFAULT_NAME_TEMPLATE}
    )
    assert result["type"] == "create_entry"
    assert result["data"][OPT_LABELS] == []


async def test_options_flow_accepts_an_empty_label_list(hass: HomeAssistant) -> None:
    flow_id = await _open_options_flow_id(hass)
    result = await hass.config_entries.options.async_configure(
        flow_id, user_input={OPT_NAME_TEMPLATE: DEFAULT_NAME_TEMPLATE, OPT_LABELS: []}
    )
    assert result["type"] == "create_entry"
    assert result["data"][OPT_LABELS] == []
