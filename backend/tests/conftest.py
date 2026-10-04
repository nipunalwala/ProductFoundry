import pytest

from productfoundry.core.run_input import RunInput


def run_input_data(**overrides) -> dict:
    data = {
        "mode": "alternative",
        "idea": "A simpler UPI expense tracker",
        "target_users": "Salaried people in Indian metros",
        "platforms": ["android", "ios"],
        "region": "IN",
        "incumbent": {
            "name": "Walnut",
            "urls": ["https://walnut.example"],
            "store_ids": {"google_play": "com.example.walnut", "app_store": "123456789"},
        },
    }
    data.update(overrides)
    return data


@pytest.fixture
def run_input() -> RunInput:
    return RunInput.model_validate(run_input_data())
