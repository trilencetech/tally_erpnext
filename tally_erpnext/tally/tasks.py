"""
Scheduled Tasks for Tally Sync

This module contains background tasks that run on schedule to sync data
between Tally and ERPNext.
"""

import frappe
from frappe import _
from frappe.utils import now


def sync_all_from_tally():
	"""
	Main scheduled task to sync all data from Tally to ERPNext, for every
	client (Customer) that has a Tally Settings row - tally_erpnext is a
	central connector shared by every client, so this can no longer assume
	a single settings row. Runs based on scheduler_events in hooks.py.
	"""
	frappe.logger("tally_sync").info(f"[{now()}] Tally sync task triggered")

	tenants = frappe.get_all(
		"Tally Settings",
		filters={"enabled": 1, "auto_sync": 1},
		fields=["name", "default_company", "sync_ledgers", "sync_stock_items", "sync_vouchers"],
	)
	if not tenants:
		frappe.logger("tally_sync").info(f"[{now()}] No tenant has Tally auto-sync enabled - skipping")
		return

	# Import sync functions
	from tally_erpnext.tally.doctype_sync import (
		sync_ledgers_from_tally,
		sync_stock_items_from_tally,
		sync_vouchers_from_tally
	)

	for settings in tenants:
		tenant = settings.name
		company = settings.get("default_company")
		frappe.logger("tally_sync").info(f"[{now()}] Starting sync for tenant {tenant}, company: {company or 'All Companies'}")

		try:
			if settings.get("sync_ledgers", True):
				frappe.enqueue(
					sync_ledgers_from_tally,
					queue="long",
					timeout=600,
					tenant=tenant,
					company=company,
					job_name=f"tally_sync_ledgers_{tenant}"
				)

			if settings.get("sync_stock_items", True):
				frappe.enqueue(
					sync_stock_items_from_tally,
					queue="long",
					timeout=600,
					tenant=tenant,
					company=company,
					job_name=f"tally_sync_stock_items_{tenant}"
				)

			if settings.get("sync_vouchers", True):
				frappe.enqueue(
					sync_vouchers_from_tally,
					queue="long",
					timeout=600,
					tenant=tenant,
					job_name=f"tally_sync_vouchers_{tenant}"
				)

			frappe.logger("tally_sync").info(f"[{now()}] Sync jobs enqueued for tenant {tenant}")

		except Exception as e:
			frappe.logger("tally_sync").error(f"[{now()}] Tally sync failed for tenant {tenant}: {str(e)}")
			frappe.log_error(
				message=str(e),
				title=_("Tally Scheduled Sync Failed - {0}").format(tenant)
			)
