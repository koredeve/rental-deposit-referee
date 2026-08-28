# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
from dataclasses import dataclass
import json


ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
ERROR_LLM = "[LLM_ERROR]"

STATUS_FUNDED = "funded"
STATUS_EVIDENCE = "evidence"
STATUS_SETTLED = "settled"

PCT_DENOMINATOR = u256(100)
PCT_TOLERANCE = 10


def _parse_llm_json(text) -> dict:
	import re
	if isinstance(text, dict):
		return text
	s = str(text)
	first = s.find("{")
	last = s.rfind("}")
	if first == -1 or last <= first:
		raise gl.vm.UserError(f"{ERROR_LLM} no JSON object found in LLM output")
	s = s[first : last + 1]
	s = re.sub(r",(?!\s*?[\{\[\"\'\w])", "", s)
	try:
		parsed = json.loads(s)
	except Exception:
		raise gl.vm.UserError(f"{ERROR_LLM} malformed JSON from LLM")
	if not isinstance(parsed, dict):
		raise gl.vm.UserError(f"{ERROR_LLM} non-dict JSON from LLM")
	return parsed


def _handle_leader_error(leaders_res, leader_fn) -> bool:
	leader_msg = leaders_res.message if hasattr(leaders_res, "message") else ""
	try:
		leader_fn()
		return False
	except gl.vm.UserError as e:
		validator_msg = e.message if hasattr(e, "message") else str(e)
		if validator_msg.startswith(ERROR_EXPECTED) or validator_msg.startswith(ERROR_EXTERNAL):
			return validator_msg == leader_msg
		if validator_msg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
			return True
		return False
	except Exception:
		return False


@gl.evm.contract_interface
class _Recipient:
	class View:
		pass

	class Write:
		pass


@allow_storage
@dataclass
class Lease:
	landlord: Address
	tenant: Address
	terms: str
	deposit_atto: u256
	status: str
	tenant_share_pct: u256
	damages: str


class RentalDepositReferee(gl.Contract):
	leases: TreeMap[str, Lease]
	move_in_photos: TreeMap[str, DynArray[str]]
	move_out_photos: TreeMap[str, DynArray[str]]
	credits: TreeMap[Address, u256]
	lease_ids: DynArray[str]

	def __init__(self) -> None:
		pass

	def _get_lease(self, lease_id: str) -> Lease:
		lease = self.leases.get(lease_id)
		if lease is None:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Unknown lease id")
		return lease

	def _require_party(self, lease: Lease) -> None:
		sender = gl.message.sender_address
		if sender != lease.landlord and sender != lease.tenant:
			raise gl.vm.UserError(
				f"{ERROR_EXPECTED} Only landlord or tenant may call this method"
			)

	def _photos_to_text(self, photos: DynArray[str]) -> str:
		parts = []
		for photo in photos:
			parts.append(str(photo))
		if len(parts) == 0:
			return "(none)"
		return "; ".join(parts)

	@gl.public.write.payable
	def create_lease(self, lease_id: str, tenant: Address, terms: str) -> None:
		if gl.message.value == u256(0):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Send value with the call")
		clean_id = str(lease_id).strip()
		clean_terms = str(terms).strip()
		if not clean_id or not clean_terms:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Lease id and terms must not be empty")
		tenant_addr = Address(tenant)
		if gl.message.sender_address == tenant_addr:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Landlord and tenant must be different addresses")
		if clean_id in self.leases:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Lease id already exists")
		self.leases[clean_id] = Lease(
			landlord=gl.message.sender_address,
			tenant=tenant_addr,
			terms=clean_terms,
			deposit_atto=u256(gl.message.value),
			status=STATUS_FUNDED,
			tenant_share_pct=u256(0),
			damages="",
		)
		self.move_in_photos[clean_id] = []
		self.move_out_photos[clean_id] = []
		self.lease_ids.append(clean_id)

	@gl.public.write
	def submit_move_in(self, lease_id: str, photos: DynArray[str]) -> None:
		lease = self._get_lease(lease_id)
		self._require_party(lease)
		if lease.status == STATUS_SETTLED:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Lease already settled")
		if len(photos) == 0:
			raise gl.vm.UserError(
				f"{ERROR_EXPECTED} At least one move-in photo description is required"
			)
		bucket = self.move_in_photos[lease_id]
		if len(bucket) > 0:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Move-in evidence already submitted")
		for i in range(len(photos)):
			bucket.append(photos[i])
		lease.status = STATUS_EVIDENCE

	@gl.public.write
	def submit_move_out(self, lease_id: str, photos: DynArray[str]) -> None:
		lease = self._get_lease(lease_id)
		self._require_party(lease)
		if lease.status == STATUS_SETTLED:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Lease already settled")
		if len(photos) == 0:
			raise gl.vm.UserError(
				f"{ERROR_EXPECTED} At least one move-out photo description is required"
			)
		bucket = self.move_out_photos[lease_id]
		if len(bucket) > 0:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Move-out evidence already submitted")
		for i in range(len(photos)):
			bucket.append(photos[i])
		lease.status = STATUS_EVIDENCE

	@gl.public.write
	def settle(self, lease_id: str) -> None:
		lease = self._get_lease(lease_id)
		self._require_party(lease)
		if lease.status == STATUS_SETTLED:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Lease already settled")
		if lease.status != STATUS_EVIDENCE:
			raise gl.vm.UserError(
				f"{ERROR_EXPECTED} Settlement requires move-in and move-out evidence"
			)
		in_photos = self.move_in_photos[lease_id]
		out_photos = self.move_out_photos[lease_id]
		if len(in_photos) == 0 or len(out_photos) == 0:
			raise gl.vm.UserError(
				f"{ERROR_EXPECTED} Settlement requires move-in and move-out evidence"
			)

		terms_text = str(lease.terms)
		in_text = self._photos_to_text(in_photos)
		out_text = self._photos_to_text(out_photos)

		def leader_fn() -> dict:
			prompt = (
				"You arbitrate a rental deposit dispute.\n"
				f"LEASE TERMS: <terms>{terms_text}</terms>\n"
				f"MOVE-IN: <in>{in_text}</in>\n"
				f"MOVE-OUT: <out>{out_text}</out>\n"
				"Estimate the tenant's responsibility as 0-100 percent. "
				'Reply JSON {"tenant_share_pct": <int>, "damages": "short summary", '
				'"reasoning": "..."}'
			)
			analysis = gl.nondet.exec_prompt(prompt, response_format="json")
			parsed = _parse_llm_json(analysis)
			raw = None
			for key in ("tenant_share_pct", "share_pct", "percentage"):
				if key in parsed:
					raw = parsed[key]
					break
			if raw is None:
				raise gl.vm.UserError(
					f"{ERROR_LLM} missing tenant share percentage in LLM output"
				)
			try:
				pct = int(float(raw))
			except Exception:
				raise gl.vm.UserError(
					f"{ERROR_LLM} non-numeric tenant share percentage in LLM output"
				)
			if pct < 0:
				pct = 0
			if pct > 100:
				pct = 100
			damages = parsed.get("damages", "")
			return {"pct": int(pct), "damages": str(damages)}

		def validator_fn(leaders_res: gl.vm.Result) -> bool:
			if not isinstance(leaders_res, gl.vm.Return):
				return _handle_leader_error(leaders_res, leader_fn)
			leader_data = leaders_res.calldata
			fresh = leader_fn()
			leader_pct = int(leader_data.get("pct", -1000))
			fresh_pct = int(fresh.get("pct", -1001))
			return abs(leader_pct - fresh_pct) <= PCT_TOLERANCE

		result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

		pct_val = u256(int(result["pct"]))
		deposit = lease.deposit_atto
		tenant_amount = deposit * pct_val // PCT_DENOMINATOR
		landlord_amount = deposit - tenant_amount

		tenant_addr = lease.tenant
		landlord_addr = lease.landlord

		lease.tenant_share_pct = pct_val
		lease.damages = str(result["damages"])
		lease.status = STATUS_SETTLED

		self.credits[tenant_addr] = self.credits.get(tenant_addr, u256(0)) + tenant_amount
		self.credits[landlord_addr] = (
			self.credits.get(landlord_addr, u256(0)) + landlord_amount
		)

	@gl.public.write
	def withdraw(self) -> None:
		who = gl.message.sender_address
		amount = self.credits.get(who, u256(0))
		if amount == u256(0):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Nothing to withdraw")
		self.credits[who] = u256(0)
		_Recipient(who).emit_transfer(value=u256(amount))

	@gl.public.view
	def get_lease(self, lease_id: str) -> dict:
		lease = self._get_lease(lease_id)
		return {
			"landlord": str(lease.landlord),
			"tenant": str(lease.tenant),
			"terms": lease.terms,
			"deposit_atto": lease.deposit_atto,
			"status": lease.status,
			"tenant_share_pct": lease.tenant_share_pct,
			"damages": lease.damages,
			"move_in_count": u256(len(self.move_in_photos[lease_id])),
			"move_out_count": u256(len(self.move_out_photos[lease_id])),
		}

	@gl.public.view
	def credit_of(self, who: Address) -> u256:
		return self.credits.get(Address(who), u256(0))

	@gl.public.view
	def total_leases(self) -> u256:
		return u256(len(self.lease_ids))
