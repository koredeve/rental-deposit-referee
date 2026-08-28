import json

DEPOSIT = 20 * 10**18
TENANT_SHARE = 5 * 10**18
LANDLORD_SHARE = 15 * 10**18

TERMS = "No pets. Tenant is responsible for damage beyond normal wear and tear."

PROMPT_REGEX = r"You arbitrate a rental deposit dispute"


def _deploy(direct_deploy):
    return direct_deploy("contracts/RentalDepositReferee.py")


def _create_lease(direct_vm, contract, alice, bob):
    direct_vm.sender = alice
    direct_vm.value = DEPOSIT
    contract.create_lease("lease-1", bob, TERMS)
    direct_vm.value = 0


def _submit_both_evidence_sets(direct_vm, contract, alice, bob):
    direct_vm.sender = alice
    contract.submit_move_in("lease-1", ["bedroom walls clean", "living room floor clean"])
    with direct_vm.prank(bob):
        contract.submit_move_out("lease-1", ["stain on living room carpet"])


def test_create_lease_stores_state_and_rejects_duplicate_id(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Landlord funds a lease and the stored state is visible via views."""
    contract = _deploy(direct_deploy)
    _create_lease(direct_vm, contract, direct_alice, direct_bob)

    lease = contract.get_lease("lease-1")
    assert len(lease["landlord"]) > 0
    assert len(lease["tenant"]) > 0
    assert lease["terms"] == TERMS
    assert lease["deposit_atto"] == DEPOSIT
    assert lease["status"] == "funded"
    assert lease["tenant_share_pct"] == 0
    assert lease["damages"] == ""
    assert lease["move_in_count"] == 0
    assert lease["move_out_count"] == 0
    assert contract.total_leases() == 1

    direct_vm.value = DEPOSIT
    with direct_vm.expect_revert("Lease id already exists"):
        contract.create_lease("lease-1", direct_bob, TERMS)
    direct_vm.value = 0
    assert contract.total_leases() == 1


def test_create_lease_requires_nonzero_deposit(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Creating a lease without a deposit is rejected."""
    contract = _deploy(direct_deploy)
    direct_vm.sender = direct_alice
    direct_vm.value = 0
    with direct_vm.expect_revert("[EXPECTED]"):
        contract.create_lease("lease-zero", direct_bob, TERMS)
    assert contract.total_leases() == 0


def test_submission_access_rules(direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
    """Only the lease parties may submit, on a known lease, with evidence."""
    contract = _deploy(direct_deploy)
    _create_lease(direct_vm, contract, direct_alice, direct_bob)
    direct_vm.sender = direct_alice

    with direct_vm.expect_revert("Unknown lease id"):
        contract.submit_move_in("missing-lease", ["photo"])

    with direct_vm.prank(direct_charlie):
        with direct_vm.expect_revert("Only landlord or tenant"):
            contract.submit_move_in("lease-1", ["photo"])

    with direct_vm.expect_revert("At least one move-in photo"):
        contract.submit_move_in("lease-1", [])

    with direct_vm.prank(direct_charlie):
        with direct_vm.expect_revert("Only landlord or tenant"):
            contract.submit_move_out("lease-1", ["photo"])

    with direct_vm.expect_revert("At least one move-out photo"):
        contract.submit_move_out("lease-1", [])


def test_move_in_and_move_out_track_status_and_counts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Evidence submissions flip the lease into the evidence phase exactly once per side."""
    contract = _deploy(direct_deploy)
    _create_lease(direct_vm, contract, direct_alice, direct_bob)

    _submit_both_evidence_sets(direct_vm, contract, direct_alice, direct_bob)

    lease = contract.get_lease("lease-1")
    assert lease["status"] == "evidence"
    assert lease["move_in_count"] == 2
    assert lease["move_out_count"] == 1

    with direct_vm.expect_revert("Move-in evidence already submitted"):
        contract.submit_move_in("lease-1", ["duplicate"])

    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Move-out evidence already submitted"):
            contract.submit_move_out("lease-1", ["duplicate"])


def test_settle_splits_deposit_by_ai_percentage(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    """Settlement credits 25% of the deposit to the tenant and the rest to the landlord."""
    contract = _deploy(direct_deploy)
    _create_lease(direct_vm, contract, direct_alice, direct_bob)
    _submit_both_evidence_sets(direct_vm, contract, direct_alice, direct_bob)

    direct_vm.mock_llm(
        PROMPT_REGEX,
        json.dumps({"tenant_share_pct": 25, "damages": "small stain", "reasoning": "minor wear"}),
    )

    direct_vm.sender = direct_alice
    contract.settle("lease-1")

    assert contract.credit_of(direct_bob) == TENANT_SHARE
    assert contract.credit_of(direct_alice) == LANDLORD_SHARE
    assert contract.credit_of(direct_charlie) == 0

    lease = contract.get_lease("lease-1")
    assert lease["status"] == "settled"
    assert lease["tenant_share_pct"] == 25
    assert lease["damages"] == "small stain"


def test_settle_requires_both_evidence_sets(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Settling before both evidence sets exist is rejected."""
    contract = _deploy(direct_deploy)
    _create_lease(direct_vm, contract, direct_alice, direct_bob)

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Settlement requires move-in and move-out evidence"):
        contract.settle("lease-1")

    contract.submit_move_in("lease-1", ["only move-in so far"])
    with direct_vm.expect_revert("Settlement requires move-in and move-out evidence"):
        contract.settle("lease-1")


def test_settle_twice_is_reverted(direct_vm, direct_deploy, direct_alice, direct_bob):
    """A settled lease cannot be settled again."""
    contract = _deploy(direct_deploy)
    _create_lease(direct_vm, contract, direct_alice, direct_bob)
    _submit_both_evidence_sets(direct_vm, contract, direct_alice, direct_bob)

    direct_vm.mock_llm(
        PROMPT_REGEX,
        json.dumps({"tenant_share_pct": 25, "damages": "small stain", "reasoning": "minor wear"}),
    )

    direct_vm.sender = direct_bob
    contract.settle("lease-1")
    with direct_vm.expect_revert("Lease already settled"):
        contract.settle("lease-1")

    assert contract.credit_of(direct_bob) == TENANT_SHARE
    assert contract.credit_of(direct_alice) == LANDLORD_SHARE


def test_percentage_alias_zero_awards_full_deposit_to_landlord(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """The LLM may answer with the `percentage` alias; 0 keeps the whole deposit with the landlord."""
    contract = _deploy(direct_deploy)
    _create_lease(direct_vm, contract, direct_alice, direct_bob)
    _submit_both_evidence_sets(direct_vm, contract, direct_alice, direct_bob)

    direct_vm.mock_llm(
        PROMPT_REGEX,
        json.dumps({"percentage": 0, "damages": "no damage", "reasoning": "normal wear"}),
    )

    direct_vm.sender = direct_alice
    contract.settle("lease-1")

    assert contract.credit_of(direct_bob) == 0
    assert contract.credit_of(direct_alice) == DEPOSIT
    assert contract.get_lease("lease-1")["tenant_share_pct"] == 0


def test_malformed_llm_output_raises_user_error(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Unparseable LLM output surfaces as an [LLM_ERROR] UserError and leaves the lease unsettled."""
    contract = _deploy(direct_deploy)
    _create_lease(direct_vm, contract, direct_alice, direct_bob)
    _submit_both_evidence_sets(direct_vm, contract, direct_alice, direct_bob)

    direct_vm.mock_llm(PROMPT_REGEX, "Sorry, I cannot help with that.")

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[LLM_ERROR]"):
        contract.settle("lease-1")

    assert contract.get_lease("lease-1")["status"] == "evidence"
    assert contract.credit_of(direct_bob) == 0


def test_landlord_cannot_be_tenant(direct_vm, direct_deploy, direct_alice):
    """A landlord cannot create a lease with themselves as the tenant."""
    contract = _deploy(direct_deploy)
    direct_vm.sender = direct_alice
    direct_vm.value = DEPOSIT

    with direct_vm.expect_revert("Landlord and tenant must be different addresses"):
        contract.create_lease("lease-self", direct_alice, "standard terms")


def test_empty_lease_inputs_rejected(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Empty lease id or terms are rejected."""
    contract = _deploy(direct_deploy)
    direct_vm.sender = direct_alice
    direct_vm.value = DEPOSIT

    with direct_vm.expect_revert("must not be empty"):
        contract.create_lease("  ", direct_bob, "terms")

    with direct_vm.expect_revert("must not be empty"):
        contract.create_lease("lease-good", direct_bob, "   ")

