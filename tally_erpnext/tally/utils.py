"""
Tally Integration Utilities

This module provides utility functions for data mapping and synchronization
between ERPNext and Tally.

tally_erpnext is a central connector shared by every client - every function
here takes a `tenant` argument: the Customer record that identifies *which
client* (and therefore which TallyBridge agent/API key, see Tally Settings
and apps/tally_erpnext/TALLYBRIDGE.md) a call is for. This is distinct from
the ERPNext "Customer" doctype records these functions sync *as data* (a
client's own Sundry Debtors) - same doctype, different meaning; don't confuse
the two.
"""

import frappe
from frappe import _
from tally_erpnext.tally.client import TallyClient


def get_tally_client(tenant):
	"""
	Get a configured TallyClient instance for one tenant (client)

	Args:
		tenant: Customer record identifying the client

	Returns:
		TallyClient: Configured Tally client
	"""
	return TallyClient(customer=tenant)


def sync_customers(tenant):
	"""
	Sync customers from Tally to ERPNext

	This function fetches ledgers from Tally under "Sundry Debtors" group
	and creates/updates corresponding Customer records in ERPNext - these are
	`tenant`'s own debtors, not other tally_erpnext clients.

	Args:
		tenant: Customer record identifying the client whose Tally this reads from

	Returns:
		dict: Sync results with counts
	"""
	client = get_tally_client(tenant)
	ledgers = client.get_ledgers()

	created = 0
	updated = 0
	skipped = 0

	# Filter ledgers that are customers (Sundry Debtors)
	customer_ledgers = [
		ledger for ledger in ledgers
		if ledger.get("parent") == "Sundry Debtors"
	]

	for ledger in customer_ledgers:
		try:
			customer_name = ledger.get("name")
			if not customer_name:
				skipped += 1
				continue

			# Check if customer already exists
			if frappe.db.exists("Customer", {"customer_name": customer_name}):
				# Update existing customer
				customer = frappe.get_doc("Customer", {"customer_name": customer_name})
				customer.tally_ledger_name = customer_name
				if ledger.get("mobile"):
					customer.mobile_no = ledger.get("mobile")
				if ledger.get("email"):
					customer.email_id = ledger.get("email")
				customer.save(ignore_permissions=True)
				updated += 1
			else:
				# Create new customer
				customer = frappe.get_doc({
					"doctype": "Customer",
					"customer_name": customer_name,
					"customer_type": "Company",
					"customer_group": "Commercial",
					"territory": "All Territories",
					"tally_ledger_name": customer_name,
					"mobile_no": ledger.get("mobile"),
					"email_id": ledger.get("email")
				})
				customer.insert(ignore_permissions=True)
				created += 1

			frappe.db.commit()

		except Exception as e:
			frappe.log_error(
				message=f"Error syncing customer {ledger.get('name')}: {str(e)}",
				title=_("Customer Sync Error")
			)
			skipped += 1
			continue

	return {
		"created": created,
		"updated": updated,
		"skipped": skipped,
		"total": len(customer_ledgers)
	}


def sync_items(tenant):
	"""
	Sync stock items from Tally to ERPNext

	This function fetches stock items from Tally and creates/updates
	corresponding Item records in ERPNext.

	Args:
		tenant: Customer record identifying the client whose Tally this reads from

	Returns:
		dict: Sync results with counts
	"""
	client = get_tally_client(tenant)
	stock_items = client.get_stock_items()

	created = 0
	updated = 0
	skipped = 0

	for tally_item in stock_items:
		try:
			item_name = tally_item.get("name")
			if not item_name:
				skipped += 1
				continue

			# Check if item already exists
			if frappe.db.exists("Item", {"item_name": item_name}):
				# Update existing item
				item = frappe.get_doc("Item", {"item_name": item_name})
				item.tally_item_name = item_name
				if tally_item.get("base_units"):
					item.stock_uom = tally_item.get("base_units")
				item.save(ignore_permissions=True)
				updated += 1
			else:
				# Create new item
				item = frappe.get_doc({
					"doctype": "Item",
					"item_code": item_name,
					"item_name": item_name,
					"item_group": tally_item.get("parent", "Products"),
					"stock_uom": tally_item.get("base_units", "Nos"),
					"tally_item_name": item_name,
					"is_stock_item": 1
				})
				item.insert(ignore_permissions=True)
				created += 1

			frappe.db.commit()

		except Exception as e:
			frappe.log_error(
				message=f"Error syncing item {tally_item.get('name')}: {str(e)}",
				title=_("Item Sync Error")
			)
			skipped += 1
			continue

	return {
		"created": created,
		"updated": updated,
		"skipped": skipped,
		"total": len(stock_items)
	}


def push_sales_invoice(sales_invoice_name, tenant):
	"""
	Push a Sales Invoice from ERPNext to Tally as a Sales Voucher

	Args:
		sales_invoice_name: Name of the Sales Invoice document
		tenant: Customer record identifying which client's Tally to push to -
			there's no automatic way to infer this from the invoice alone, so
			the caller must supply it (e.g. from a Company -> tenant mapping)

	Returns:
		dict: Push result
	"""
	client = get_tally_client(tenant)

	# Get Sales Invoice document
	si = frappe.get_doc("Sales Invoice", sales_invoice_name)

	# Prepare ledger entries for Tally
	ledger_entries = []

	# Customer ledger entry (Debit)
	ledger_entries.append({
		"ledger_name": si.customer_name,
		"amount": si.grand_total,
		"is_debit": True
	})

	# Sales ledger entry (Credit)
	ledger_entries.append({
		"ledger_name": "Sales",
		"amount": si.total,
		"is_debit": False
	})

	# Tax ledger entries (if any)
	for tax in si.taxes:
		ledger_entries.append({
			"ledger_name": tax.account_head,
			"amount": tax.tax_amount,
			"is_debit": False
		})

	# Create voucher in Tally (using Journal type as Sales is not yet supported)
	# Convert date to YYYYMMDD format
	from datetime import datetime
	posting_date_str = si.posting_date
	if isinstance(posting_date_str, str):
		date_obj = datetime.strptime(posting_date_str, "%Y-%m-%d")
		tally_date = date_obj.strftime("%Y%m%d")
	else:
		tally_date = posting_date_str.strftime("%Y%m%d")

	response = client.create_voucher(
		voucher_type="Journal",  # Using Journal as Sales voucher not yet implemented
		date=tally_date,
		ledger_entries=ledger_entries,
		narration=f"Sales Invoice {si.name} - {si.customer_name}"
	)

	# Update Sales Invoice with Tally reference
	si.db_set("tally_voucher_number", response.get("voucher_number"), update_modified=False)
	frappe.db.commit()

	return {
		"sales_invoice": si.name,
		"tally_response": response
	}


def map_erpnext_customer_to_tally_ledger(customer_name):
	"""
	Map ERPNext Customer to Tally Ledger format

	Args:
		customer_name: Name of the Customer document

	Returns:
		dict: Tally ledger data
	"""
	customer = frappe.get_doc("Customer", customer_name)

	return {
		"name": customer.customer_name,
		"parent": "Sundry Debtors",
		"address": customer.primary_address or "",
		"mobile": customer.mobile_no or "",
		"email": customer.email_id or ""
	}


def map_erpnext_item_to_tally_stock_item(item_code):
	"""
	Map ERPNext Item to Tally Stock Item format

	Args:
		item_code: Item code in ERPNext

	Returns:
		dict: Tally stock item data
	"""
	item = frappe.get_doc("Item", item_code)

	return {
		"name": item.item_name,
		"category": item.item_group,
		"unit": item.stock_uom  # Will be mapped to base_unit in create_stock_item
	}


def create_customer_in_tally(customer_name, tenant):
	"""
	Create a customer (debtor ledger) in Tally from an ERPNext Customer

	Args:
		customer_name: Name of the Customer document (the debtor)
		tenant: Customer record identifying which client's Tally to create it in

	Returns:
		dict: Creation response
	"""
	client = get_tally_client(tenant)
	ledger_data = map_erpnext_customer_to_tally_ledger(customer_name)

	response = client.create_ledger(**ledger_data)

	# Update customer with Tally reference
	frappe.db.set_value("Customer", customer_name, "tally_ledger_name", ledger_data["name"])
	frappe.db.commit()

	return response


def create_item_in_tally(item_code, tenant):
	"""
	Create an item in Tally from ERPNext Item

	Args:
		item_code: Item code in ERPNext
		tenant: Customer record identifying which client's Tally to create it in

	Returns:
		dict: Creation response
	"""
	client = get_tally_client(tenant)
	item_data = map_erpnext_item_to_tally_stock_item(item_code)

	response = client.create_stock_item(**item_data)

	# Update item with Tally reference
	frappe.db.set_value("Item", item_code, "tally_item_name", item_data["name"])
	frappe.db.commit()

	return response


def is_tally_enabled(tenant):
	"""
	Check if Tally integration is enabled for one tenant (client)

	Args:
		tenant: Customer record identifying the client

	Returns:
		bool: True if enabled, False otherwise
	"""
	settings = TallyClient.get_tally_settings(tenant)
	return settings.get("enabled", False)


def validate_tally_connection(tenant):
	"""
	Validate connection to one tenant's Tally server

	Args:
		tenant: Customer record identifying the client

	Returns:
		tuple: (is_valid, message)
	"""
	try:
		client = get_tally_client(tenant)
		if client.test_connection():
			return True, _("Connection successful")
		else:
			return False, _("Connection failed")
	except Exception as e:
		return False, str(e)
