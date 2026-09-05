"""Config + options flow for the Battery Notes glue.

A single instance is all that's needed (it watches all Battery Notes devices), so the
config flow is a one-click confirm. Behaviour is tuned in the options flow: the task
name templates, two-way sync, clear-on-recovery, and what a rechargeable battery earns.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    DEFAULT_CHARGE_NAME_TEMPLATE,
    DEFAULT_CLEAR_ON_RECOVERY,
    DEFAULT_NAME_TEMPLATE,
    DEFAULT_NOT_REPORTED_DAYS,
    DEFAULT_TREAT_NOT_REPORTED,
    DEFAULT_TWO_WAY,
    DOMAIN,
    MANAGED_DISPLAY_NAME,
    OPT_CHARGE_NAME_TEMPLATE,
    OPT_CLEAR_ON_RECOVERY,
    OPT_NAME_TEMPLATE,
    OPT_NOT_REPORTED_DAYS,
    OPT_RECHARGEABLE_MODE,
    OPT_TREAT_NOT_REPORTED,
    OPT_TWO_WAY,
    RECHARGEABLE_MODES,
)
from .logic import resolve_rechargeable_mode


class BatteryNotesGlueConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-instance config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # One glue instance watches every Battery Notes device — disallow a second.
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title=MANAGED_DISPLAY_NAME, data={})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BatteryNotesGlueOptionsFlow()


class BatteryNotesGlueOptionsFlow(OptionsFlow):
    """Options: name templates, two-way sync, clear-on-recovery, rechargeable mode."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    OPT_NAME_TEMPLATE,
                    default=opts.get(OPT_NAME_TEMPLATE, DEFAULT_NAME_TEMPLATE),
                ): str,
                vol.Optional(
                    OPT_TWO_WAY,
                    default=opts.get(OPT_TWO_WAY, DEFAULT_TWO_WAY),
                ): bool,
                vol.Optional(
                    OPT_CLEAR_ON_RECOVERY,
                    default=opts.get(OPT_CLEAR_ON_RECOVERY, DEFAULT_CLEAR_ON_RECOVERY),
                ): bool,
                # Resolved rather than read straight out of options, so an entry still
                # carrying the old skip_rechargeable boolean opens on the mode it is
                # actually getting (see logic.resolve_rechargeable_mode).
                vol.Optional(
                    OPT_RECHARGEABLE_MODE,
                    default=resolve_rechargeable_mode(opts),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=RECHARGEABLE_MODES,
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=OPT_RECHARGEABLE_MODE,
                    )
                ),
                vol.Optional(
                    OPT_CHARGE_NAME_TEMPLATE,
                    default=opts.get(
                        OPT_CHARGE_NAME_TEMPLATE, DEFAULT_CHARGE_NAME_TEMPLATE
                    ),
                ): str,
                vol.Optional(
                    OPT_TREAT_NOT_REPORTED,
                    default=opts.get(
                        OPT_TREAT_NOT_REPORTED, DEFAULT_TREAT_NOT_REPORTED
                    ),
                ): bool,
                vol.Optional(
                    OPT_NOT_REPORTED_DAYS,
                    default=opts.get(
                        OPT_NOT_REPORTED_DAYS, DEFAULT_NOT_REPORTED_DAYS
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=1)),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            # The two template fields' descriptions name the token to type,
            # ``{device_name}``. Home Assistant renders a description through a message
            # formatter, which reads that as a variable and — given none — replaces the
            # whole sentence with a MISSING_VALUE error. Supplying it as a real
            # placeholder whose value is the literal text is how Home Assistant expects
            # this to be done; escaping the braces instead is what hassfest rejects.
            description_placeholders={"device_name": "{device_name}"},
        )
