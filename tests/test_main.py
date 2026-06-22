import app.main as main


def test_configure_event_loop_policy_uses_selector_on_windows(
    monkeypatch,
) -> None:
    selector_policy = object()
    configured: list[object] = []

    monkeypatch.setattr(main.sys, "platform", "win32")
    monkeypatch.setattr(
        main.asyncio,
        "WindowsSelectorEventLoopPolicy",
        lambda: selector_policy,
        raising=False,
    )
    monkeypatch.setattr(
        main.asyncio,
        "set_event_loop_policy",
        configured.append,
    )

    main.configure_event_loop_policy()

    assert configured == [selector_policy]
