# Rental Deposit Referee

Fair security-deposit settlement without the argument. The landlord funds the lease on-chain (deposit held by the contract), both parties upload photo descriptions at move-in and move-out, and AI validators arbitrate how much of the deposit the tenant keeps based on the lease terms.

## Architecture

- **User action:** landlord creates a funded lease; parties submit evidence photo sets; either party triggers settlement.
- **Evidence source:** free-text descriptions of move-in / move-out photos supplied by both sides.
- **Non-deterministic call:** `gl.nondet.exec_prompt()` asks the model for the tenant's responsibility percentage given terms + both evidence sets.
- **Equivalence principle:** custom validator reruns the judgment independently; results agree only within ±10 percentage points. Malformed LLM output raises `[LLM_ERROR]`; business rules raise `[EXPECTED]`.
- **Settlement effect:** deposit split pro-rata into internal credits; each party withdraws their share to their own address.
- **Appeal path:** native GenLayer appeal window applies before finalization of the settle transaction.

## Quickstart

```bash
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
genvm-lint check contracts/RentalDepositReferee.py --json
pytest tests/direct/ -v
```

Integration smoke (needs a network): `gltest tests/integration/ -v -s`.

## Interface

| Method | Type | Notes |
|---|---|---|
| `create_lease(lease_id, tenant, terms)` | write payable | value = deposit |
| `submit_move_in(lease_id, photos)` | write | parties only, once |
| `submit_move_out(lease_id, photos)` | write | parties only, once |
| `settle(lease_id)` | write | nondet arbitration, ±10pp tolerance |
| `withdraw()` | write | pull your credits |
| `get_lease(lease_id)` / `credit_of(who)` / `total_leases()` | view | state reads |

## StudioNet

StudioNet is gasless — a 0 GEN balance is expected and sufficient.
