def test_escaped_model_id_is_not_reinterpreted_as_query_or_fragment(make_proxy):
    with make_proxy() as (client, calls):
        assert client.get("/v1/models/model%3Fspecial%23id?extra=true").status_code == 200
        assert calls[0].url.raw_path == b"/v1/models/model%3Fspecial%23id?extra=true"
