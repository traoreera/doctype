"""Plugin de TEST `mailer` : la preuve qu'un plugin apporte ses propres nœuds et ses propres déclencheurs à l'éditeur d'automatisations.

- `mail.send` (action IPC, ouverte à xflow) : « envoie » un e-mail en l'ajoutant à `outbox.jsonl` et rend son `message_id` — une
  étape suivante peut le reprendre (`{{ steps.<étape>.result.message_id }}`).
- `POST <préfixe>/mailer/receive` : « reçoit » un e-mail pour l'organisation de l'appelant et émet l'événement `mailer.received`
  (avec `tenant_id` : xflow ne lance alors que les flux de cette organisation).
- `mail.log` (action IPC NON décrite) : celle qu'une organisation décrit elle-même dans le Desk (nœud d'organisation).
- `on_load` passe sa section `xflow:` au moteur, qui la sert à l'éditeur (`GET /flow-catalog`).
"""

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from xcore.sdk import AutoDispatchMixin, TrustedBase, action, ok

OUTBOX = Path(__file__).resolve().parent.parent / "outbox.jsonl"


class Plugin(AutoDispatchMixin, TrustedBase):
    async def on_load(self):
        self.get_service("doctype_registry").declare_xflow(self.ctx.name, (self.ctx.config or {}).get("xflow"))
        writers = self.get_service("doctype_writer")
        router = APIRouter()

        @router.post("/receive")
        async def receive(request: Request, body: dict) -> dict:
            who = await writers.as_request(request)  # l'utilisateur et SON organisation, jamais pris du corps
            await self.ctx.events.emit(
                "mailer.received",
                {"tenant_id": who.context.tenant_id, "sender": str(body.get("sender", "")), "subject": str(body.get("subject", ""))},
                source=self.ctx.name,
            )
            return {"received": True}

        self._router = router

    def get_router(self):
        return getattr(self, "_router", None)

    async def on_unload(self):
        self.get_service("doctype_registry").unregister_owner(self.ctx.name)

    @action("mail.send")
    async def send(self, payload: dict) -> dict:
        message_id = uuid.uuid4().hex
        line = {"message_id": message_id, "to": payload.get("to"), "subject": payload.get("subject"), "body": payload.get("body")}
        with OUTBOX.open("a", encoding="utf-8") as out:
            out.write(json.dumps(line, ensure_ascii=False) + "\n")
        return ok(message_id=message_id)

    @action("mail.log")
    async def log(self, payload: dict) -> dict:
        """Une action que le plugin ne DÉCRIT pas : l'organisation la décrit dans le Desk, et l'éditeur la montre comme un nœud."""
        entry = uuid.uuid4().hex
        with OUTBOX.open("a", encoding="utf-8") as out:
            out.write(json.dumps({"log": entry, "note": payload.get("note")}, ensure_ascii=False) + "\n")
        return ok(entry=entry)
