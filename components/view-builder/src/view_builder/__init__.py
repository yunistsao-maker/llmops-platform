"""view-builder: compiles the portal view model (contracts/fixtures/portal-view.sample.json shape).

Inputs (curated bucket, contracts/storage-layout.md): the current release's data files, the editorial overlay,
the current security results and the SEC-01..32 matrix. Output: portal/views/{view_id}/portal.json and the
pointer portal/views/current.json.
"""
__version__ = "0.1.0"
