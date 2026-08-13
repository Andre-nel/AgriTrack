def test_core_route_inventory(app):
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}

    assert "health" in endpoints
    assert "auth.login" in endpoints
    assert "web.dashboard" in endpoints
    assert "web.analytics_lsu_paddock_tracking" in endpoints
    assert "web.analytics_water_assets" in endpoints
    assert "tasks.index" in endpoints
    assert "calendar.index" in endpoints
    assert "finance.cash_flow" in endpoints
    assert "simulator.index" in endpoints
    assert "wiki.index" in endpoints
    assert "ops.index" in endpoints
    assert "api_v1.health" in endpoints
    assert "mobile_api.bootstrap" in endpoints
    assert "farms.list_farms" in endpoints
    assert "paddocks.list_paddocks" in endpoints
    assert "mobs.list_mobs" in endpoints
    assert "water_api.list_water_assets" in endpoints
