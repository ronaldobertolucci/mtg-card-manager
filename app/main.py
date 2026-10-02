from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.api import router
from app.database import lifespan
from app.translation_api import router as translation_router

app = FastAPI(
    title="MTG Card Manager",
    version="0.1.0",
    description=(
        "Local Scryfall oracle catalog with custom translations. Catalog reading is public "
        "and requires no authentication, independently of login in the frontend. "
        "Translation write authorization is a separate policy."
    ),
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(translation_router)


@app.exception_handler(RequestValidationError)
async def query_validation_error(
    request: Request, exception: RequestValidationError
) -> JSONResponse:
    if request.url.path == "/cards/search":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": jsonable_encoder(exception.errors())},
        )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": jsonable_encoder(exception.errors())},
    )


@app.get("/health", tags=["operations"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


def custom_openapi() -> dict:
    if app.openapi_schema is None:
        schema = get_openapi(
            title=app.title, version=app.version, description=app.description, routes=app.routes
        )
        # The search validation handler returns 400, never FastAPI's default 422.
        schema["paths"]["/cards/search"]["get"]["responses"].pop("422", None)
        app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi
