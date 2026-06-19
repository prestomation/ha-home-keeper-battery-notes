"""Config + options flow for the Battery Notes glue.

A single instance is all that's needed (it watches all Battery Notes devices), so the
config flow is a one-click confirm. Behaviour is tuned in the options flow: the task
name template, two-way sync, and clear-on-recovery.
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

from .const import (
    DEFAULT_CLEAR_ON_RECOVERY,
    DEFAULT_NAME_TEMPLATE,
    DEFAULT_NOT_REPORTED_DAYS,
    DEFAULT_TREAT_NOT_REPORTED,
    DEFAULT_TWO_WAY,
    DOMAIN,
    MANAGED_DISPLAY_NAME,
    OPT_CLEAR_ON_RECOVERY,
    OPT_NAME_TEMPLATE,
    OPT_NOT_REPORTED_DAYS,
    OPT_TREAT_NOT_REPORTED,
    OPT_TWO_WAY,
)


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
    """Options: name template, two-way sync, clear-on-recovery."""

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
        return self.async_show_form(step_id="init", data_schema=schema)
