from api.billing_mapping_routes import _contract_account_id, _items, classify_product


def test_classify_product_states():
    active = {"status": "ACTIVE", "accountId": "acct_1"}
    assert classify_product(None, None, "acct_1") == "contract_missing"
    assert classify_product("ctr_1", None, "acct_1") == "invalid_contract"
    assert classify_product("ctr_1", {**active, "status": "FINISHED"}, "acct_1") == "inactive_contract"
    assert classify_product("ctr_1", active, None) == "account_not_linked"
    assert classify_product("ctr_1", active, "acct_2") == "account_mismatch"
    assert classify_product("ctr_1", active, "acct_1") == "correctly_linked"


def test_nested_account_and_list_response_shapes():
    assert _contract_account_id({"account": {"id": "acct_1"}}) == "acct_1"
    assert _items({"data": {"content": [{"id": "ctr_1"}]}}) == [{"id": "ctr_1"}]
