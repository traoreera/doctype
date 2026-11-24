"""Routes des tâches de fond (jobs.py, traoreera/doctype#71) : LIRE leur état, et relancer une tâche échouée.

Rien ne se DÉPOSE par HTTP : une tâche est déposée par le code de confiance d'un plugin (un hook, un bouton serveur), qui
sait ce qu'elle fait et avec quel payload. L'API ne montre que l'état — jamais le payload — à l'auteur de la tâche et à qui
porte le grant que le plugin a nommé en la déclarant.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..context import ContextSettings, RequestContext, http_context
from ..errors import DoctypeError
from ..jobs import MAX_LIST, JobService


def build_jobs_router(jobs: JobService, settings: ContextSettings) -> APIRouter:
    ctx_dep = http_context(settings)
    router = APIRouter(dependencies=[Depends(ctx_dep)])

    @router.get("/jobs")
    async def list_jobs(
        name: str | None = Query(None, max_length=140),
        key: str | None = Query(None, max_length=255),
        status: str | None = Query(None, max_length=16),
        limit: int = Query(50, ge=1, le=MAX_LIST),
        ctx: RequestContext = Depends(ctx_dep),
    ):
        """Les tâches que l'appelant peut voir (les siennes, et celles dont il porte le grant), les plus récentes d'abord."""
        try:
            return await jobs.list(ctx, name=name, key=key, status=status, limit=limit)
        except DoctypeError as e:
            _raise(e)

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: str, ctx: RequestContext = Depends(ctx_dep)):
        try:
            return await jobs.get(ctx, job_id)
        except DoctypeError as e:
            _raise(e)

    @router.post("/jobs/{job_id}/retry")
    async def retry_job(job_id: str, ctx: RequestContext = Depends(ctx_dep)):
        """Relance une tâche ÉCHOUÉE (compteur à zéro). Réservé à qui porte le grant de la tâche."""
        try:
            return await jobs.retry(ctx, job_id)
        except DoctypeError as e:
            _raise(e)

    return router


def _raise(e: DoctypeError) -> None:
    raise HTTPException(status_code=e.status, detail=e.as_detail()) from e
