from __future__ import annotations


def test_admin_can_create_agency(admin_client):
    resp = admin_client.post(
        "/api/admin/agencies",
        json={"agency_name": "New Agency", "email": "new@agency.example", "password": "starter-pw"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["agency_name"] == "New Agency"
    assert body["email"] == "new@agency.example"
    assert "password" not in body
    assert "password_hash" not in body


def test_created_agency_can_log_in(admin_client, api_client):
    admin_client.post(
        "/api/admin/agencies",
        json={"agency_name": "New Agency", "email": "new@agency.example", "password": "starter-pw"},
    )
    resp = api_client.post("/api/login", json={"email": "new@agency.example", "password": "starter-pw"})
    assert resp.status_code == 200


def test_admin_can_list_agencies(admin_client):
    admin_client.post(
        "/api/admin/agencies",
        json={"agency_name": "Agency One", "email": "one@agency.example", "password": "pw"},
    )
    resp = admin_client.get("/api/admin/agencies")
    assert resp.status_code == 200
    names = [a["agency_name"] for a in resp.json()]
    assert "Agency One" in names


def test_admin_can_deactivate_agency(admin_client, api_client):
    create_resp = admin_client.post(
        "/api/admin/agencies",
        json={"agency_name": "Agency One", "email": "one@agency.example", "password": "pw"},
    )
    agency_id = create_resp.json()["id"]

    deactivate_resp = admin_client.patch(f"/api/admin/agencies/{agency_id}", json={"is_active": False})
    assert deactivate_resp.status_code == 200

    login_resp = api_client.post("/api/login", json={"email": "one@agency.example", "password": "pw"})
    assert login_resp.status_code == 401


def test_admin_can_reset_agency_password(admin_client, api_client):
    create_resp = admin_client.post(
        "/api/admin/agencies",
        json={"agency_name": "Agency One", "email": "one@agency.example", "password": "old-pw"},
    )
    agency_id = create_resp.json()["id"]

    reset_resp = admin_client.patch(f"/api/admin/agencies/{agency_id}", json={"new_password": "new-pw"})
    assert reset_resp.status_code == 200

    old_login = api_client.post("/api/login", json={"email": "one@agency.example", "password": "old-pw"})
    assert old_login.status_code == 401

    new_login = api_client.post("/api/login", json={"email": "one@agency.example", "password": "new-pw"})
    assert new_login.status_code == 200


def test_non_admin_cannot_manage_agencies(agency_client):
    resp = agency_client.get("/api/admin/agencies")
    assert resp.status_code == 401
