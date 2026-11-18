# Copyright (c) 2025, SVNIX Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class TallySettings(Document):
	"""Tally Settings DocType for managing Tally integration configuration"""

	def validate(self):
		"""Validate Tally settings"""
		if not self.enabled:
			return
		if self.connection_mode == "Direct (Same Network)":
			if not self.host:
				frappe.throw(_("Tally Server Host is required when Tally integration is enabled"))
			if not self.port:
				frappe.throw(_("Tally Server Port is required when Tally integration is enabled"))
		else:
			if not self.agent_id:
				frappe.throw(_("Agent ID is required for Relay (TallyBridge) mode"))
			if not self.api_key:
				frappe.throw(_("API Key is required for Relay (TallyBridge) mode"))

	def on_update(self):
		"""Clear cache when settings are updated"""
		frappe.cache().delete_value("tally_settings")
