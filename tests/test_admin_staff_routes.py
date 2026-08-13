from __future__ import annotations


def _create_agency(admin_client) -> int:
    resp = admin_client.post(
        "/api/admin/agencies",
        json={"agency_name": "Thornes", "email": "thornes@agency.example", "password": "pw"},
    )
    return resp.json()["id"]


def test_admin_can_create_staff_member(admin_client):
    agency_id = _create_agency(admin_client)
    resp = admin_client.post(
        f"/api/admin/agencies/{agency_id}/staff",
        json={"staff_name": "Jane Doe"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["staff_name"] == "Jane Doe"
    assert body["agent_id"] == agency_id
    assert body["heygen_avatar_id"] is None
    assert body["voice_consent_confirmed"] is False


def test_admin_can_set_voice_with_consent(admin_client):
    agency_id = _create_agency(admin_client)
    create_resp = admin_client.post(
        f"/api/admin/agencies/{agency_id}/staff", json={"staff_name": "Jane Doe"}
    )
    staff_id = create_resp.json()["id"]

    resp = admin_client.patch(
        f"/api/admin/agencies/{agency_id}/staff/{staff_id}",
        json={"elevenlabs_voice_id": "voice-123", "voice_consent_confirmed": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["elevenlabs_voice_id"] == "voice-123"
    assert body["voice_consent_confirmed"] is True


def test_admin_cannot_set_voice_without_consent(admin_client):
    agency_id = _create_agency(admin_client)
    create_resp = admin_client.post(
        f"/api/admin/agencies/{agency_id}/staff", json={"staff_name": "Jane Doe"}
    )
    staff_id = create_resp.json()["id"]

    resp = admin_client.patch(
        f"/api/admin/agencies/{agency_id}/staff/{staff_id}",
        json={"elevenlabs_voice_id": "voice-123", "voice_consent_confirmed": False},
    )
    assert resp.status_code == 400


def test_admin_can_list_staff(admin_client):
    agency_id = _create_agency(admin_client)
    admin_client.post(f"/api/admin/agencies/{agency_id}/staff", json={"staff_name": "Jane Doe"})
    admin_client.post(f"/api/admin/agencies/{agency_id}/staff", json={"staff_name": "John Smith"})

    resp = admin_client.get(f"/api/admin/agencies/{agency_id}/staff")
    assert resp.status_code == 200
    names = [s["staff_name"] for s in resp.json()]
    assert names == ["Jane Doe", "John Smith"]


def test_admin_can_delete_staff(admin_client):
    agency_id = _create_agency(admin_client)
    create_resp = admin_client.post(
        f"/api/admin/agencies/{agency_id}/staff", json={"staff_name": "Jane Doe"}
    )
    staff_id = create_resp.json()["id"]

    resp = admin_client.delete(f"/api/admin/agencies/{agency_id}/staff/{staff_id}")
    assert resp.status_code == 200

    list_resp = admin_client.get(f"/api/admin/agencies/{agency_id}/staff")
    assert list_resp.json() == []


def test_admin_cannot_exceed_five_staff(admin_client):
    agency_id = _create_agency(admin_client)
    for i in range(5):
        resp = admin_client.post(
            f"/api/admin/agencies/{agency_id}/staff", json={"staff_name": f"Staff {i}"}
        )
        assert resp.status_code == 201

    resp = admin_client.post(
        f"/api/admin/agencies/{agency_id}/staff", json={"staff_name": "Staff 6"}
    )
    assert resp.status_code == 400


def test_non_admin_cannot_manage_staff(agency_client):
    resp = agency_client.get("/api/admin/agencies/1/staff")
    assert resp.status_code == 401


def test_admin_can_update_agency_branding_colors(admin_client):
    agency_id = _create_agency(admin_client)
    resp = admin_client.patch(
        f"/api/admin/agencies/{agency_id}",
        json={"primary_color": "#ff0000", "secondary_color": "#00ff00"},
    )
    assert resp.status_code == 200
    assert resp.json()["primary_color"] == "#ff0000"
    assert resp.json()["secondary_color"] == "#00ff00"
