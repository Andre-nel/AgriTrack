from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import (
    AnimalGroupType,
    Farm,
    Shearer,
    ShearingBale,
    ShearingBaleCode,
    ShearingEntry,
    ShearingSession,
)
from app.services.stock_service import StockService
from app.services.task_service import TaskService


_UNCHANGED = object()


class ShearingService:
    MAX_NAME_LENGTH = 120
    MAX_CODE_LENGTH = 60
    MAX_TRAIT_LENGTH = 80
    MAX_SHORT_TRAIT_LENGTH = 20
    MAX_LONG_TRAIT_LENGTH = 120
    MAX_NOTE_LENGTH = 2000
    SESSION_STATUSES = ("open", "closed")
    SPECIES = ("Sheep", "Goat")
    PRICING_INPUT_MODES = ("unpriced", "price_per_kg", "total_price", "both")
    MONEY_QUANT = Decimal("0.01")
    RATE_QUANT = Decimal("0.0001")
    WEIGHT_QUANT = Decimal("0.001")
    TRAIT_QUANT = Decimal("0.01")

    @classmethod
    def normalize_shearer_name(cls, name: str | None) -> str:
        text = " ".join((name or "").strip().split())
        if not text:
            raise ValueError("Shearer name is required")
        if len(text) > cls.MAX_NAME_LENGTH:
            raise ValueError(f"Shearer name must be {cls.MAX_NAME_LENGTH} characters or fewer")
        return text

    @classmethod
    def name_key(cls, name: str | None) -> str:
        return cls.normalize_shearer_name(name).casefold()

    @classmethod
    def normalize_bale_code(cls, code: str | None) -> str:
        text = " ".join((code or "").strip().split())
        if not text:
            raise ValueError("Bale code is required")
        if len(text) > cls.MAX_CODE_LENGTH:
            raise ValueError(f"Bale code must be {cls.MAX_CODE_LENGTH} characters or fewer")
        return text

    @classmethod
    def bale_code_key(cls, code: str | None) -> str:
        return cls.normalize_bale_code(code).casefold()

    @classmethod
    def normalize_species(cls, species: str | None) -> str:
        normalized = StockService.normalize_species(species or "")
        if normalized not in cls.SPECIES:
            raise ValueError("Shearing species must be Sheep or Goat")
        return normalized

    @staticmethod
    def parse_date(value, field_name: str) -> date:
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be a valid ISO date") from exc

    @staticmethod
    def optional_date(value, field_name: str) -> date | None:
        if value in (None, ""):
            return None
        return ShearingService.parse_date(value, field_name)

    @staticmethod
    def parse_money(value, field_name: str) -> Decimal:
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be a valid amount") from exc
        if parsed < 0:
            raise ValueError(f"{field_name} must be greater than or equal to 0")
        return parsed.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @classmethod
    def parse_decimal(
        cls,
        value,
        field_name: str,
        *,
        quant: Decimal,
        allow_zero: bool = True,
    ) -> Decimal:
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be a valid number") from exc
        if allow_zero:
            if parsed < 0:
                raise ValueError(f"{field_name} must be greater than or equal to 0")
        elif parsed <= 0:
            raise ValueError(f"{field_name} must be greater than 0")
        return parsed.quantize(quant, rounding=ROUND_HALF_UP)

    @classmethod
    def parse_optional_decimal(
        cls,
        value,
        field_name: str,
        *,
        quant: Decimal,
        allow_zero: bool = True,
    ) -> Decimal | None:
        if value in (None, ""):
            return None
        return cls.parse_decimal(value, field_name, quant=quant, allow_zero=allow_zero)

    @classmethod
    def parse_optional_percent(cls, value, field_name: str) -> Decimal | None:
        parsed = cls.parse_optional_decimal(
            value,
            field_name,
            quant=cls.TRAIT_QUANT,
        )
        if parsed is None:
            return None
        if parsed > 100:
            raise ValueError(f"{field_name} must be 100 or less")
        return parsed

    @staticmethod
    def parse_quantity(value) -> int:
        try:
            quantity = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Quantity must be a whole number") from exc
        if quantity < 0:
            raise ValueError("Quantity must be greater than or equal to 0")
        return quantity

    @classmethod
    def create_shearer(
        cls,
        *,
        farm_id: str,
        name: str | None,
        shearer_id: str | None = None,
        active: bool = True,
    ) -> Shearer:
        farm = db.session.get(Farm, farm_id)
        if farm is None:
            raise ValueError("Farm is invalid")
        normalized_name = cls.normalize_shearer_name(name)
        key = normalized_name.casefold()
        if shearer_id:
            existing_by_id = db.session.get(Shearer, str(shearer_id))
            if existing_by_id is not None:
                duplicate = Shearer.query.filter(
                    Shearer.name_key == key,
                    Shearer.id != str(shearer_id),
                ).first()
                if duplicate is not None:
                    raise ValueError("Shearer name already exists")
                existing_by_id.name = normalized_name
                existing_by_id.name_key = key
                existing_by_id.active = active
                return existing_by_id
        duplicate = Shearer.query.filter_by(name_key=key).first()
        if duplicate is not None:
            if shearer_id and str(duplicate.id) != str(shearer_id):
                raise ValueError("Shearer name already exists")
            duplicate.name = normalized_name
            duplicate.active = active
            return duplicate
        values = {
            "name": normalized_name,
            "name_key": key,
            "active": active,
        }
        if shearer_id:
            values["id"] = str(shearer_id)
        shearer = Shearer(**values)
        db.session.add(shearer)
        db.session.flush()
        return shearer

    @classmethod
    def set_shearer_active(cls, shearer: Shearer, active: bool) -> Shearer:
        shearer.active = active
        db.session.flush()
        return shearer

    @classmethod
    def upsert_bale_code(
        cls,
        *,
        species: str | None,
        code: str | None,
        bale_code_id: str | None = None,
        active: bool = True,
        line_type: str | None = None,
        age_group: str | None = None,
        fineness_grade: str | None = None,
        length_code: str | None = None,
        fineness_micron=None,
        clean_yield_percent=None,
        color: str | None = None,
        vegetable_matter: str | None = None,
        style_character: str | None = None,
        consistency: str | None = None,
        fault: str | None = None,
        description: str | None = None,
        notes: str | None = None,
    ) -> ShearingBaleCode:
        normalized_species = cls.normalize_species(species)
        normalized_code = cls.normalize_bale_code(code)
        key = normalized_code.casefold()
        existing_by_id = db.session.get(ShearingBaleCode, str(bale_code_id)) if bale_code_id else None
        duplicate = ShearingBaleCode.query.filter(
            ShearingBaleCode.species == normalized_species,
            ShearingBaleCode.code_key == key,
        ).first()
        if duplicate is not None and existing_by_id is not None and str(duplicate.id) != str(existing_by_id.id):
            raise ValueError("Bale code already exists for this species")

        bale_code = existing_by_id or duplicate
        if bale_code is None:
            values = {"species": normalized_species, "code": normalized_code, "code_key": key}
            if bale_code_id:
                values["id"] = str(bale_code_id)
            bale_code = ShearingBaleCode(**values)
            db.session.add(bale_code)
        elif bale_code.bales and bale_code.species != normalized_species:
            raise ValueError("Bale code species cannot be changed after bales are recorded")

        bale_code.species = normalized_species
        bale_code.code = normalized_code
        bale_code.code_key = key
        bale_code.active = bool(active)
        bale_code.line_type = TaskService.optional_text(line_type, cls.MAX_TRAIT_LENGTH)
        bale_code.age_group = TaskService.optional_text(age_group, cls.MAX_TRAIT_LENGTH)
        bale_code.fineness_grade = TaskService.optional_text(fineness_grade, cls.MAX_TRAIT_LENGTH)
        bale_code.length_code = TaskService.optional_text(
            str(length_code or "").upper(),
            cls.MAX_SHORT_TRAIT_LENGTH,
        )
        bale_code.fineness_micron = cls.parse_optional_decimal(
            fineness_micron,
            "Fineness micron",
            quant=cls.TRAIT_QUANT,
            allow_zero=False,
        )
        bale_code.clean_yield_percent = cls.parse_optional_percent(
            clean_yield_percent,
            "Clean yield",
        )
        bale_code.color = TaskService.optional_text(color, cls.MAX_TRAIT_LENGTH)
        bale_code.vegetable_matter = TaskService.optional_text(vegetable_matter, cls.MAX_TRAIT_LENGTH)
        bale_code.style_character = TaskService.optional_text(
            style_character,
            cls.MAX_LONG_TRAIT_LENGTH,
        )
        bale_code.consistency = TaskService.optional_text(consistency, cls.MAX_LONG_TRAIT_LENGTH)
        bale_code.fault = TaskService.optional_text(fault, cls.MAX_LONG_TRAIT_LENGTH)
        bale_code.description = TaskService.optional_text(description, cls.MAX_NOTE_LENGTH)
        bale_code.notes = TaskService.optional_text(notes, cls.MAX_NOTE_LENGTH)
        db.session.flush()
        return bale_code

    @classmethod
    def set_bale_code_active(cls, bale_code: ShearingBaleCode, active: bool) -> ShearingBaleCode:
        bale_code.active = active
        db.session.flush()
        return bale_code

    @classmethod
    def create_session(
        cls,
        *,
        farm_id: str,
        name: str | None,
        species: str | None,
        start_date,
        end_date=None,
        lootjie_rate=None,
        notes: str | None = None,
        session_id: str | None = None,
    ) -> ShearingSession:
        farm = db.session.get(Farm, farm_id)
        if farm is None:
            raise ValueError("Farm is invalid")
        values = {"farm_id": farm_id}
        if session_id:
            values["id"] = str(session_id)
        session = ShearingSession(**values)
        db.session.add(session)
        return cls.update_session(
            session,
            name=name,
            species=species,
            start_date=start_date,
            end_date=end_date,
            lootjie_rate=lootjie_rate,
            notes=notes,
            status="open",
        )

    @classmethod
    def update_session(
        cls,
        session: ShearingSession,
        *,
        name: str | None = None,
        species: str | None = None,
        start_date=None,
        end_date=None,
        lootjie_rate=None,
        notes: str | None = None,
        status: str | None = None,
    ) -> ShearingSession:
        if name is not None:
            session.name = TaskService.require_text(name, "Session name", cls.MAX_NAME_LENGTH)
        if species is not None:
            next_species = cls.normalize_species(species)
            if (session.entries or session.bales) and next_species != session.species:
                raise ValueError("Session species cannot be changed after counts or bales are recorded")
            session.species = next_species
        if start_date is not None:
            session.start_date = cls.parse_date(start_date, "start_date")
        if end_date is not None:
            session.end_date = cls.optional_date(end_date, "end_date")
        if lootjie_rate is not None:
            session.lootjie_rate = cls.parse_money(lootjie_rate, "Lootjie rate")
        if notes is not None:
            session.notes = TaskService.optional_text(notes, cls.MAX_NOTE_LENGTH)
        if status is not None:
            normalized_status = str(status).strip().lower()
            if normalized_status not in cls.SESSION_STATUSES:
                raise ValueError("Session status must be open or closed")
            session.status = normalized_status

        if not session.name:
            raise ValueError("Session name is required")
        if not session.species:
            raise ValueError("Session species is required")
        if session.start_date is None:
            raise ValueError("Session start date is required")
        if session.end_date is not None and session.end_date < session.start_date:
            raise ValueError("Session end date cannot be before the start date")
        if session.lootjie_rate is None:
            raise ValueError("Lootjie rate is required")
        cls._validate_entries_within_session_dates(session)
        db.session.flush()
        return session

    @classmethod
    def close_session(cls, session: ShearingSession) -> ShearingSession:
        session.status = "closed"
        db.session.flush()
        return session

    @classmethod
    def reopen_session(cls, session: ShearingSession) -> ShearingSession:
        session.status = "open"
        db.session.flush()
        return session

    @classmethod
    def group_type_from_payload(cls, payload: dict, *, expected_species: str) -> AnimalGroupType:
        group_id = str(payload.get("animal_group_type_id") or "").strip()
        if group_id:
            group_type = db.session.get(AnimalGroupType, group_id)
            if group_type is None:
                raise ValueError("animal_group_type_id is invalid")
        else:
            group_payload = payload.get("animal_group_type")
            if not isinstance(group_payload, dict):
                raise ValueError("animal_group_type_id or animal_group_type is required")
            breed = " ".join(str(group_payload.get("breed") or "").strip().split())
            if not breed:
                raise ValueError("Breed is required")
            group_type = StockService.get_or_create_group_type(
                species=group_payload.get("species"),
                breed=breed,
                sex=group_payload.get("sex"),
                age_class=group_payload.get("age_class"),
            )
        if group_type.species != expected_species:
            raise ValueError("Animal type species must match the shearing session species")
        return group_type

    @classmethod
    def record_entry(
        cls,
        *,
        session: ShearingSession,
        work_date,
        shearer_id: str,
        animal_group_type: AnimalGroupType,
        quantity,
        note: str | None = None,
        entry_id: str | None = None,
        unit_rate=_UNCHANGED,
        line_amount=_UNCHANGED,
    ) -> ShearingEntry | None:
        entry = db.session.get(ShearingEntry, str(entry_id)) if entry_id else None
        if entry is not None and str(entry.session_id) != str(session.id):
            raise ValueError("Shearing entry does not belong to this shearing session")
        if session.status == "closed" and entry is None:
            raise ValueError("Cannot add counts against a closed shearing session")
        date_value = cls.parse_date(work_date, "work_date")
        cls.validate_work_date(session, date_value)
        shearer = db.session.get(Shearer, str(shearer_id or ""))
        if shearer is None:
            raise ValueError("Shearer is invalid")
        if not shearer.active and (entry is None or str(entry.shearer_id) != str(shearer.id)):
            raise ValueError("Shearer is inactive")
        if animal_group_type.species != session.species:
            raise ValueError("Animal type species must match the shearing session species")
        quantity_value = cls.parse_quantity(quantity)
        matching_entry = ShearingEntry.query.filter_by(
            session_id=session.id,
            work_date=date_value,
            shearer_id=shearer.id,
            animal_group_type_id=animal_group_type.id,
        ).first()
        if quantity_value == 0:
            entry = entry or matching_entry
            if entry is not None:
                db.session.delete(entry)
                db.session.flush()
            return None

        if entry is not None and matching_entry is not None and str(matching_entry.id) != str(entry.id):
            raise ValueError("A shearing entry already exists for this date, shearer, and animal type")
        if entry is None:
            entry = matching_entry

        note_text = TaskService.optional_text(note, cls.MAX_NOTE_LENGTH)
        if entry is None:
            values = {
                "session_id": session.id,
                "work_date": date_value,
                "shearer_id": shearer.id,
                "animal_group_type_id": animal_group_type.id,
            }
            if entry_id:
                values["id"] = str(entry_id)
            entry = ShearingEntry(**values)
            db.session.add(entry)
        else:
            entry.work_date = date_value
            entry.shearer = shearer
            entry.animal_group_type = animal_group_type
            entry.shearer_id = shearer.id
            entry.animal_group_type_id = animal_group_type.id
        entry.quantity = quantity_value
        entry.note = note_text
        cls.apply_entry_rate_overrides(
            entry,
            session=session,
            animal_group_type=animal_group_type,
            unit_rate=unit_rate,
            line_amount=line_amount,
        )
        db.session.flush()
        return entry

    @classmethod
    def delete_entry(cls, *, session: ShearingSession, entry_id: str) -> ShearingEntry | None:
        entry = db.session.get(ShearingEntry, str(entry_id or ""))
        if entry is None:
            return None
        if str(entry.session_id) != str(session.id):
            raise ValueError("Shearing entry does not belong to this shearing session")
        db.session.delete(entry)
        db.session.flush()
        return entry

    @classmethod
    def apply_entry_rate_overrides(
        cls,
        entry: ShearingEntry,
        *,
        session: ShearingSession,
        animal_group_type: AnimalGroupType,
        unit_rate=_UNCHANGED,
        line_amount=_UNCHANGED,
    ) -> None:
        default_rate = cls.unit_rate(session, animal_group_type)
        if unit_rate is not _UNCHANGED:
            parsed_rate = cls.parse_optional_decimal(
                unit_rate,
                "Rate",
                quant=cls.MONEY_QUANT,
            )
            entry.unit_rate_override = None if parsed_rate is None or parsed_rate == default_rate else parsed_rate

        effective_rate = cls.entry_unit_rate(session, entry, animal_group_type=animal_group_type)
        if line_amount is not _UNCHANGED:
            parsed_amount = cls.parse_optional_decimal(
                line_amount,
                "Amount",
                quant=cls.MONEY_QUANT,
            )
            calculated_amount = (effective_rate * Decimal(int(entry.quantity))).quantize(
                cls.MONEY_QUANT,
                rounding=ROUND_HALF_UP,
            )
            entry.line_amount_override = (
                None if parsed_amount is None or parsed_amount == calculated_amount else parsed_amount
            )

    @classmethod
    def bale_code_from_payload(cls, payload: dict, *, expected_species: str) -> ShearingBaleCode | None:
        bale_code_id = str(payload.get("bale_code_id") or "").strip()
        if bale_code_id:
            bale_code = db.session.get(ShearingBaleCode, bale_code_id)
            if bale_code is None:
                raise ValueError("bale_code_id is invalid")
            if bale_code.species != expected_species:
                raise ValueError("Bale code species must match the shearing session species")
            return bale_code

        code_payload = payload.get("bale_code")
        if isinstance(code_payload, dict):
            bale_code = cls.upsert_bale_code(
                bale_code_id=code_payload.get("id"),
                species=code_payload.get("species") or expected_species,
                code=code_payload.get("code"),
                active=code_payload.get("active", True),
                line_type=code_payload.get("line_type"),
                age_group=code_payload.get("age_group"),
                fineness_grade=code_payload.get("fineness_grade"),
                length_code=code_payload.get("length_code"),
                fineness_micron=code_payload.get("fineness_micron"),
                clean_yield_percent=code_payload.get("clean_yield_percent"),
                color=code_payload.get("color"),
                vegetable_matter=code_payload.get("vegetable_matter"),
                style_character=code_payload.get("style_character"),
                consistency=code_payload.get("consistency"),
                fault=code_payload.get("fault"),
                description=code_payload.get("description"),
                notes=code_payload.get("notes"),
            )
            if bale_code.species != expected_species:
                raise ValueError("Bale code species must match the shearing session species")
            return bale_code

        return None

    @classmethod
    def record_bale(
        cls,
        *,
        session: ShearingSession,
        bale_id: str | None = None,
        bale_code: ShearingBaleCode | None = None,
        code_text: str | None = None,
        bale_number: str | None = None,
        weight_kg=None,
        price_per_kg=None,
        total_price=None,
        notes: str | None = None,
    ) -> ShearingBale:
        if bale_code is not None and bale_code.species != session.species:
            raise ValueError("Bale code species must match the shearing session species")

        normalized_code = cls.normalize_bale_code(code_text or (bale_code.code if bale_code else None))
        weight_value = cls.parse_decimal(
            weight_kg,
            "Bale weight",
            quant=cls.WEIGHT_QUANT,
            allow_zero=False,
        )
        price_per_kg_value, total_price_value, input_mode = cls.derive_bale_prices(
            weight_value=weight_value,
            price_per_kg=price_per_kg,
            total_price=total_price,
        )

        bale = db.session.get(ShearingBale, str(bale_id)) if bale_id else None
        if bale is not None and str(bale.session_id) != str(session.id):
            raise ValueError("Bale does not belong to this shearing session")
        if bale is None:
            values = {"session": session}
            if bale_id:
                values["id"] = str(bale_id)
            bale = ShearingBale(**values)
            db.session.add(bale)

        bale.bale_code = bale_code
        bale.code_text = normalized_code
        bale.code_key = normalized_code.casefold()
        bale.bale_number = TaskService.optional_text(bale_number, cls.MAX_CODE_LENGTH)
        bale.weight_kg = weight_value
        bale.price_per_kg = price_per_kg_value
        bale.total_price = total_price_value
        bale.pricing_input_mode = input_mode
        bale.notes = TaskService.optional_text(notes, cls.MAX_NOTE_LENGTH)
        db.session.flush()
        return bale

    @classmethod
    def delete_bale(cls, *, session: ShearingSession, bale_id: str) -> ShearingBale | None:
        bale = db.session.get(ShearingBale, str(bale_id or ""))
        if bale is None:
            return None
        if str(bale.session_id) != str(session.id):
            raise ValueError("Bale does not belong to this shearing session")
        db.session.delete(bale)
        db.session.flush()
        return bale

    @classmethod
    def derive_bale_prices(cls, *, weight_value: Decimal, price_per_kg, total_price) -> tuple[Decimal | None, Decimal | None, str]:
        price_per_kg_value = cls.parse_optional_decimal(
            price_per_kg,
            "Price per kg",
            quant=cls.RATE_QUANT,
        )
        total_price_value = cls.parse_optional_decimal(
            total_price,
            "Total price",
            quant=cls.MONEY_QUANT,
        )
        if price_per_kg_value is None and total_price_value is None:
            return None, None, "unpriced"
        if price_per_kg_value is not None and total_price_value is None:
            calculated_total = (price_per_kg_value * weight_value).quantize(cls.MONEY_QUANT, rounding=ROUND_HALF_UP)
            return price_per_kg_value, calculated_total, "price_per_kg"
        if price_per_kg_value is None and total_price_value is not None:
            calculated_rate = (total_price_value / weight_value).quantize(cls.RATE_QUANT, rounding=ROUND_HALF_UP)
            return calculated_rate, total_price_value, "total_price"

        expected_total = (price_per_kg_value * weight_value).quantize(cls.MONEY_QUANT, rounding=ROUND_HALF_UP)
        if abs(expected_total - total_price_value) > cls.MONEY_QUANT:
            raise ValueError("Total price must match weight kg multiplied by price per kg")
        return price_per_kg_value, total_price_value, "both"

    @classmethod
    def validate_work_date(cls, session: ShearingSession, work_date: date) -> None:
        if work_date < session.start_date:
            raise ValueError("Work date cannot be before the session start date")
        if session.end_date is not None and work_date > session.end_date:
            raise ValueError("Work date cannot be after the session end date")

    @classmethod
    def serialize_shearer(cls, shearer: Shearer) -> dict:
        return {
            "id": str(shearer.id),
            "name": shearer.name,
            "active": bool(shearer.active),
        }

    @classmethod
    def serialize_session(cls, session: ShearingSession, *, include_entries: bool = True) -> dict:
        breakdown = cls.session_breakdown(session)
        bale_breakdown = cls.bale_money_breakdown(session)
        payload = {
            "id": str(session.id),
            "farm_id": str(session.farm_id),
            "name": session.name,
            "species": session.species,
            "start_date": session.start_date.isoformat(),
            "end_date": session.end_date.isoformat() if session.end_date else None,
            "status": session.status,
            "lootjie_rate": float(session.lootjie_rate or 0),
            "adult_old_ram_multiplier": float(session.adult_old_ram_multiplier or 2),
            "notes": session.notes,
            "totals": breakdown["totals"],
            "by_shearer": breakdown["by_shearer"],
            "by_animal_type": breakdown["by_animal_type"],
            "by_date": breakdown["by_date"],
            "bale_money_totals": bale_breakdown["totals"],
            "bale_summary_by_code": bale_breakdown["by_code"],
        }
        if include_entries:
            payload["entries"] = [cls.serialize_entry(entry, session=session) for entry in cls.sorted_entries(session.entries)]
            payload["bales"] = [cls.serialize_bale(bale) for bale in cls.sorted_bales(session.bales)]
        return payload

    @classmethod
    def serialize_entry(cls, entry: ShearingEntry, *, session: ShearingSession | None = None) -> dict:
        session = session or entry.session
        multiplier = cls.entry_multiplier(session, entry.animal_group_type)
        unit_rate = cls.entry_unit_rate(session, entry)
        amount = cls.entry_amount(session, entry)
        return {
            "id": str(entry.id),
            "session_id": str(entry.session_id),
            "work_date": entry.work_date.isoformat(),
            "shearer_id": str(entry.shearer_id),
            "shearer_name": entry.shearer.name,
            "animal_group_type_id": str(entry.animal_group_type_id),
            "animal_group_type": cls.serialize_group_type(entry.animal_group_type),
            "quantity": int(entry.quantity),
            "multiplier": float(multiplier),
            "unit_rate": float(unit_rate),
            "line_amount": float(amount),
            "unit_rate_override": float(entry.unit_rate_override) if entry.unit_rate_override is not None else None,
            "line_amount_override": (
                float(entry.line_amount_override) if entry.line_amount_override is not None else None
            ),
            "note": entry.note,
        }

    @classmethod
    def serialize_bale_code(cls, bale_code: ShearingBaleCode) -> dict:
        return {
            "id": str(bale_code.id),
            "species": bale_code.species,
            "code": bale_code.code,
            "active": bool(bale_code.active),
            "line_type": bale_code.line_type,
            "age_group": bale_code.age_group,
            "fineness_grade": bale_code.fineness_grade,
            "length_code": bale_code.length_code,
            "fineness_micron": float(bale_code.fineness_micron) if bale_code.fineness_micron is not None else None,
            "clean_yield_percent": float(bale_code.clean_yield_percent) if bale_code.clean_yield_percent is not None else None,
            "style_character": bale_code.style_character,
            "consistency": bale_code.consistency,
            "color": bale_code.color,
            "vegetable_matter": bale_code.vegetable_matter,
            "fault": bale_code.fault,
            "description": bale_code.description,
            "notes": bale_code.notes,
        }

    @classmethod
    def serialize_bale(cls, bale: ShearingBale) -> dict:
        return {
            "id": str(bale.id),
            "session_id": str(bale.session_id),
            "bale_code_id": str(bale.bale_code_id) if bale.bale_code_id else None,
            "code": bale.bale_code.code if bale.bale_code else bale.code_text,
            "code_text": bale.code_text,
            "bale_number": bale.bale_number,
            "weight_kg": float(bale.weight_kg or 0),
            "price_per_kg": float(bale.price_per_kg) if bale.price_per_kg is not None else None,
            "total_price": float(bale.total_price) if bale.total_price is not None else None,
            "pricing_input_mode": bale.pricing_input_mode,
            "notes": bale.notes,
        }

    @staticmethod
    def serialize_group_type(group_type: AnimalGroupType) -> dict:
        return {
            "id": str(group_type.id),
            "species": group_type.species,
            "breed": group_type.breed,
            "sex": group_type.sex,
            "age_class": group_type.age_class,
        }

    @classmethod
    def session_breakdown(cls, session: ShearingSession) -> dict:
        by_shearer: dict[str, dict] = {}
        by_animal: dict[str, dict] = {}
        by_date: dict[str, dict] = {}
        total_quantity = 0
        total_amount = Decimal("0.00")

        for entry in cls.sorted_entries(session.entries):
            amount = cls.entry_amount(session, entry)
            total_quantity += int(entry.quantity)
            total_amount += amount

            shearer_key = str(entry.shearer_id)
            shearer_row = by_shearer.setdefault(
                shearer_key,
                {
                    "shearer_id": shearer_key,
                    "shearer_name": entry.shearer.name,
                    "quantity": 0,
                    "amount": Decimal("0.00"),
                },
            )
            shearer_row["quantity"] += int(entry.quantity)
            shearer_row["amount"] += amount

            animal_key = str(entry.animal_group_type_id)
            animal_row = by_animal.setdefault(
                animal_key,
                {
                    "animal_group_type_id": animal_key,
                    "animal_group_type": cls.serialize_group_type(entry.animal_group_type),
                    "quantity": 0,
                    "amount": Decimal("0.00"),
                },
            )
            animal_row["quantity"] += int(entry.quantity)
            animal_row["amount"] += amount

            date_key = entry.work_date.isoformat()
            date_row = by_date.setdefault(
                date_key,
                {"work_date": date_key, "quantity": 0, "amount": Decimal("0.00")},
            )
            date_row["quantity"] += int(entry.quantity)
            date_row["amount"] += amount

        return {
            "totals": {
                "quantity": total_quantity,
                "amount": float(total_amount),
            },
            "by_shearer": cls._money_rows(by_shearer.values(), "shearer_name"),
            "by_animal_type": cls._animal_rows(by_animal.values()),
            "by_date": cls._money_rows(by_date.values(), "work_date"),
        }

    @classmethod
    def daily_breakdown(cls, session: ShearingSession) -> list[dict]:
        by_date: dict[str, dict] = {}
        for entry in cls.sorted_entries(session.entries):
            amount = cls.entry_amount(session, entry)
            date_key = entry.work_date.isoformat()
            date_row = by_date.setdefault(
                date_key,
                {
                    "work_date": date_key,
                    "quantity": 0,
                    "amount": Decimal("0.00"),
                    "animal_counts": {},
                },
            )
            date_row["quantity"] += int(entry.quantity)
            date_row["amount"] += amount

            animal_key = str(entry.animal_group_type_id)
            animal_row = date_row["animal_counts"].setdefault(
                animal_key,
                {
                    "animal_group_type_id": animal_key,
                    "animal_group_type": cls.serialize_group_type(entry.animal_group_type),
                    "label": cls.animal_group_label(entry.animal_group_type),
                    "quantity": 0,
                },
            )
            animal_row["quantity"] += int(entry.quantity)

        rows = []
        for row in sorted(by_date.values(), key=lambda item: item["work_date"]):
            animal_counts = sorted(
                row["animal_counts"].values(),
                key=lambda item: item["label"].lower(),
            )
            rows.append(
                {
                    "work_date": row["work_date"],
                    "quantity": row["quantity"],
                    "amount": float(row["amount"]),
                    "animal_counts": animal_counts,
                }
            )
        return rows

    @classmethod
    def shearing_analytics_chart_payload(
        cls,
        session: ShearingSession,
        *,
        group_by: list[str] | tuple[str, ...] | None = None,
    ) -> dict:
        dimension_order = ("date", "shearer", "animal_type")
        dimension_labels = {
            "date": "Date",
            "shearer": "Shearer",
            "animal_type": "Animal Type",
        }
        selected_dimensions = [
            dimension
            for dimension in dimension_order
            if dimension in set(group_by or ("date", "shearer"))
        ]
        if not selected_dimensions:
            selected_dimensions = ["date", "shearer"]
        series_dimensions = [dimension for dimension in dimension_order if dimension not in selected_dimensions]
        series_dimension = series_dimensions[0] if len(series_dimensions) == 1 else None

        entries = cls.sorted_entries(session.entries)
        if not entries:
            return {
                "labels": [],
                "group_by": selected_dimensions,
                "panels": [],
            }

        def dimension_value(entry: ShearingEntry, dimension: str) -> tuple[str, str, str]:
            if dimension == "date":
                value = entry.work_date.isoformat()
                return value, value, value
            if dimension == "shearer":
                return str(entry.shearer_id), entry.shearer.name, entry.shearer.name.casefold()
            label = cls.animal_group_label(entry.animal_group_type)
            return str(entry.animal_group_type_id), label, label.casefold()

        label_text: dict[tuple[str, ...], str] = {}
        label_sort: dict[tuple[str, ...], tuple[str, ...]] = {}
        series_text: dict[str, str] = {}
        series_sort: dict[str, str] = {}
        values: dict[tuple[str, ...], dict[str, int]] = {}

        for entry in entries:
            label_parts = [dimension_value(entry, dimension) for dimension in selected_dimensions]
            label_key = tuple(part[0] for part in label_parts)
            if label_key not in label_text:
                label_text[label_key] = " | ".join(part[1] for part in label_parts)
                label_sort[label_key] = tuple(part[2] for part in label_parts)

            if series_dimension is None:
                series_key = "total"
                series_text[series_key] = "Animals shorn"
                series_sort[series_key] = "animals shorn"
            else:
                series_key, series_label, sort_label = dimension_value(entry, series_dimension)
                series_text[series_key] = series_label
                series_sort[series_key] = sort_label

            values.setdefault(label_key, {})
            values[label_key][series_key] = values[label_key].get(series_key, 0) + int(entry.quantity)

        label_keys = sorted(label_text, key=lambda key: label_sort[key])
        series_keys = sorted(series_text, key=lambda key: series_sort[key])
        labels = [label_text[key] for key in label_keys]
        datasets = [
            {
                "label": series_text[series_key],
                "values": [values.get(label_key, {}).get(series_key, 0) for label_key in label_keys],
            }
            for series_key in series_keys
        ]
        group_title = " And ".join(dimension_labels[dimension] for dimension in selected_dimensions)
        if selected_dimensions == ["date", "shearer"] and series_dimension == "animal_type":
            title = "Daily Animal Counts By Shearer"
        elif series_dimension is not None:
            title = f"Animal Counts By {group_title}, Split By {dimension_labels[series_dimension]}"
        else:
            title = f"Animal Counts By {group_title}"
        return {
            "labels": labels,
            "group_by": selected_dimensions,
            "series_by": series_dimension,
            "panels": [
                {
                    "id": "daily-shearer-animal-counts",
                    "title": title,
                    "chart_type": "bar",
                    "value_format": "count",
                    "y_axis_label": "Animals shorn",
                    "datasets": datasets,
                }
            ],
        }

    @classmethod
    def bale_money_breakdown(cls, session: ShearingSession) -> dict:
        by_code: dict[str, dict] = {}
        total_bales = 0
        total_kg = Decimal("0.000")
        priced_bales = 0
        priced_kg = Decimal("0.000")
        total_price = Decimal("0.00")

        for bale in cls.sorted_bales(session.bales):
            total_bales += 1
            weight = Decimal(bale.weight_kg or 0)
            total_kg += weight
            is_priced = bale.total_price is not None
            if is_priced:
                priced_bales += 1
                priced_kg += weight
                total_price += Decimal(bale.total_price or 0)

            code_key = str(bale.bale_code_id) if bale.bale_code_id else f"ad-hoc:{bale.code_key}"
            row = by_code.setdefault(
                code_key,
                {
                    "bale_code_id": str(bale.bale_code_id) if bale.bale_code_id else None,
                    "code": bale.bale_code.code if bale.bale_code else bale.code_text,
                    "code_text": bale.code_text,
                    "bale_count": 0,
                    "bales": 0,
                    "kg": Decimal("0.000"),
                    "priced_kg": Decimal("0.000"),
                    "unpriced_bales": 0,
                    "total_price": Decimal("0.00"),
                },
            )
            row["bale_count"] += 1
            row["bales"] += 1
            row["kg"] += weight
            if is_priced:
                row["priced_kg"] += weight
                row["total_price"] += Decimal(bale.total_price or 0)
            else:
                row["unpriced_bales"] += 1

        return {
            "totals": cls._bale_totals(
                total_bales=total_bales,
                total_kg=total_kg,
                priced_bales=priced_bales,
                priced_kg=priced_kg,
                total_price=total_price,
            ),
            "by_code": cls._bale_code_rows(by_code.values()),
        }

    @classmethod
    def bale_statistics(cls, session: ShearingSession) -> dict:
        breakdown = cls.bale_money_breakdown(session)
        totals = breakdown["totals"]
        total_bales = totals["total_bales"]
        total_kg = Decimal(str(totals["total_kg"]))
        average_kg_per_bale = None
        if total_bales:
            average_kg_per_bale = float(
                (total_kg / Decimal(total_bales)).quantize(cls.WEIGHT_QUANT, rounding=ROUND_HALF_UP)
            )
        return {
            **totals,
            "average_kg_per_bale": average_kg_per_bale,
        }

    @classmethod
    def bale_chart_payload(cls, session: ShearingSession) -> dict:
        rows = cls.bale_money_breakdown(session)["by_code"]
        if not rows:
            return {"labels": [], "panels": []}

        labels = [row["code"] for row in rows]

        def average_kg(row: dict) -> float | None:
            if not row["bale_count"]:
                return None
            return float(
                (Decimal(str(row["kg"])) / Decimal(row["bale_count"])).quantize(
                    cls.WEIGHT_QUANT,
                    rounding=ROUND_HALF_UP,
                )
            )

        panels = [
            {
                "id": "bale-count",
                "title": "Bales By Code",
                "chart_type": "bar",
                "value_format": "count",
                "y_axis_label": "Bales",
                "datasets": [{"label": "Bales", "values": [row["bale_count"] for row in rows]}],
            },
            {
                "id": "bale-kg",
                "title": "Total Kg By Code",
                "chart_type": "bar",
                "value_format": "kg",
                "y_axis_label": "Kg",
                "datasets": [{"label": "Kg", "values": [row["kg"] for row in rows]}],
            },
            {
                "id": "bale-revenue",
                "title": "Revenue By Code",
                "chart_type": "bar",
                "value_format": "money",
                "y_axis_label": "Revenue",
                "datasets": [{"label": "Revenue", "values": [row["total_price"] for row in rows]}],
            },
            {
                "id": "bale-average-kg",
                "title": "Average Kg Per Bale",
                "chart_type": "bar",
                "value_format": "kg",
                "y_axis_label": "Avg kg/bale",
                "datasets": [{"label": "Avg kg/bale", "values": [average_kg(row) for row in rows]}],
            },
            {
                "id": "bale-average-price",
                "title": "Average Price Per Kg",
                "chart_type": "bar",
                "value_format": "price",
                "y_axis_label": "Average P/kg",
                "datasets": [
                    {
                        "label": "Average P/kg",
                        "values": [row["average_price_per_kg"] for row in rows],
                    }
                ],
            },
            {
                "id": "bale-unpriced",
                "title": "Unpriced Bales By Code",
                "chart_type": "bar",
                "value_format": "count",
                "y_axis_label": "Unpriced bales",
                "datasets": [{"label": "Unpriced", "values": [row["unpriced_bales"] for row in rows]}],
            },
        ]
        return {"labels": labels, "panels": panels}

    @classmethod
    def entry_amount(cls, session: ShearingSession, entry: ShearingEntry) -> Decimal:
        if entry.line_amount_override is not None:
            return Decimal(entry.line_amount_override).quantize(cls.MONEY_QUANT, rounding=ROUND_HALF_UP)
        return (cls.entry_unit_rate(session, entry) * Decimal(int(entry.quantity))).quantize(
            cls.MONEY_QUANT,
            rounding=ROUND_HALF_UP,
        )

    @classmethod
    def entry_unit_rate(
        cls,
        session: ShearingSession,
        entry: ShearingEntry,
        *,
        animal_group_type: AnimalGroupType | None = None,
    ) -> Decimal:
        if entry.unit_rate_override is not None:
            return Decimal(entry.unit_rate_override).quantize(cls.MONEY_QUANT, rounding=ROUND_HALF_UP)
        return cls.unit_rate(session, animal_group_type or entry.animal_group_type)

    @classmethod
    def unit_rate(cls, session: ShearingSession, group_type: AnimalGroupType) -> Decimal:
        return (Decimal(session.lootjie_rate or 0) * cls.entry_multiplier(session, group_type)).quantize(
            cls.MONEY_QUANT,
            rounding=ROUND_HALF_UP,
        )

    @staticmethod
    def entry_multiplier(session: ShearingSession, group_type: AnimalGroupType) -> Decimal:
        if group_type.sex == "ram" and group_type.age_class in {"adult", "old"}:
            return Decimal(session.adult_old_ram_multiplier or 2)
        return Decimal("1.00")

    @classmethod
    def animal_group_label(cls, group_type: AnimalGroupType) -> str:
        return f"{group_type.breed} {group_type.sex} {group_type.age_class}"

    @staticmethod
    def sorted_entries(entries: Iterable[ShearingEntry]) -> list[ShearingEntry]:
        return sorted(
            list(entries),
            key=lambda entry: (
                entry.work_date,
                entry.shearer.name.lower() if entry.shearer else "",
                entry.animal_group_type.species,
                entry.animal_group_type.breed,
                entry.animal_group_type.sex,
                entry.animal_group_type.age_class,
            ),
        )

    @staticmethod
    def sorted_bales(bales: Iterable[ShearingBale]) -> list[ShearingBale]:
        return sorted(
            list(bales),
            key=lambda bale: (
                (bale.bale_number or "").lower(),
                (bale.code_text or "").lower(),
                bale.created_at,
            ),
        )

    @classmethod
    def bale_codes_for_species(cls, species: str | None = None, *, active_only: bool = False) -> list[ShearingBaleCode]:
        query = ShearingBaleCode.query
        if species:
            query = query.filter_by(species=cls.normalize_species(species))
        if active_only:
            query = query.filter_by(active=True)
        return query.order_by(ShearingBaleCode.species.asc(), ShearingBaleCode.code.asc()).all()

    @classmethod
    def sessions_for_farm(cls, farm_id: str) -> list[ShearingSession]:
        return (
            ShearingSession.query.options(
                selectinload(ShearingSession.entries).selectinload(ShearingEntry.shearer),
                selectinload(ShearingSession.entries).selectinload(ShearingEntry.animal_group_type),
                selectinload(ShearingSession.bales).selectinload(ShearingBale.bale_code),
            )
            .filter_by(farm_id=farm_id)
            .order_by(ShearingSession.start_date.desc(), ShearingSession.created_at.desc())
            .all()
        )

    @staticmethod
    def _money_rows(rows: Iterable[dict], sort_key: str) -> list[dict]:
        return [
            {**row, "amount": float(row["amount"])}
            for row in sorted(rows, key=lambda item: str(item[sort_key]).lower())
        ]

    @staticmethod
    def _animal_rows(rows: Iterable[dict]) -> list[dict]:
        return [
            {**row, "amount": float(row["amount"])}
            for row in sorted(
                rows,
                key=lambda item: (
                    item["animal_group_type"]["species"],
                    item["animal_group_type"]["breed"],
                    item["animal_group_type"]["sex"],
                    item["animal_group_type"]["age_class"],
                ),
            )
        ]

    @classmethod
    def _bale_totals(
        cls,
        *,
        total_bales: int,
        total_kg: Decimal,
        priced_bales: int,
        priced_kg: Decimal,
        total_price: Decimal,
    ) -> dict:
        average_price = None
        if priced_kg > 0:
            average_price = (total_price / priced_kg).quantize(cls.RATE_QUANT, rounding=ROUND_HALF_UP)
        return {
            "total_bales": total_bales,
            "total_kg": float(total_kg.quantize(cls.WEIGHT_QUANT, rounding=ROUND_HALF_UP)),
            "priced_bales": priced_bales,
            "priced_kg": float(priced_kg.quantize(cls.WEIGHT_QUANT, rounding=ROUND_HALF_UP)),
            "unpriced_bales": total_bales - priced_bales,
            "total_price": float(total_price.quantize(cls.MONEY_QUANT, rounding=ROUND_HALF_UP)),
            "average_price_per_kg": float(average_price) if average_price is not None else None,
        }

    @classmethod
    def _bale_code_rows(cls, rows: Iterable[dict]) -> list[dict]:
        payload = []
        for row in rows:
            priced_kg = row["priced_kg"]
            average_price = None
            if priced_kg > 0:
                average_price = (row["total_price"] / priced_kg).quantize(cls.RATE_QUANT, rounding=ROUND_HALF_UP)
            payload.append(
                {
                    **row,
                    "kg": float(row["kg"].quantize(cls.WEIGHT_QUANT, rounding=ROUND_HALF_UP)),
                    "priced_kg": float(priced_kg.quantize(cls.WEIGHT_QUANT, rounding=ROUND_HALF_UP)),
                    "total_price": float(row["total_price"].quantize(cls.MONEY_QUANT, rounding=ROUND_HALF_UP)),
                    "average_price_per_kg": float(average_price) if average_price is not None else None,
                }
            )
        return sorted(payload, key=lambda item: str(item["code"]).lower())

    @staticmethod
    def _validate_entries_within_session_dates(session: ShearingSession) -> None:
        if not session.start_date:
            return
        for entry in session.entries:
            ShearingService.validate_work_date(session, entry.work_date)
