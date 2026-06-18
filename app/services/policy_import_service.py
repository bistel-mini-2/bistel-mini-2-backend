from typing import Annotated

from fastapi import Depends

from app.common.psycopg_pool_conf import psycopg_pool
from app.repositories.policy_import_repository import PolicyImportRepository
from app.schemas.policy_data_schema import PolicyImportResponse


class PolicyImportService:
    async def import_raw_policies(self) -> PolicyImportResponse:
        async with psycopg_pool.connection() as conn:
            async with conn.transaction():
                raw_count = await PolicyImportRepository.count_importable_raw_rows(conn)
                policy_count = await PolicyImportRepository.upsert_policies(conn)
                detail_count = await PolicyImportRepository.upsert_policy_details(conn)
                required_document_count = (
                    await PolicyImportRepository.replace_required_documents(conn)
                )
                tag_count = await PolicyImportRepository.replace_policy_tags(conn)
                checklist_count = (
                    await PolicyImportRepository.replace_policy_checklist_templates(conn)
                )

        return PolicyImportResponse(
            raw_count=raw_count,
            imported_policy_count=policy_count,
            imported_detail_count=detail_count,
            imported_required_document_count=required_document_count,
            imported_tag_count=tag_count,
            imported_checklist_template_count=checklist_count,
            skipped_count=max(raw_count - policy_count, 0),
        )


PolicyImportServiceDep = Annotated[
    PolicyImportService,
    Depends(PolicyImportService),
]
