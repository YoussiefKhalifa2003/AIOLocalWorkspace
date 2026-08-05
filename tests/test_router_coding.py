from app.router.classify import classify_request


def test_coding_routes_python_not_writing():
    plan = classify_request(
        "write me a python code that will return back a list of numbers reversed only give me the code"
    )
    assert plan.agents[0] == "coding"
