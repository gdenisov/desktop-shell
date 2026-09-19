from typing import Any, Final, TypeVar

from app_manifest.manifest import describe_validation_error
from flask import Blueprint, Flask, jsonify, request
from flask.typing import ResponseReturnValue
from imbue.imbue_common.frozen_model import FrozenModel
from imbue.imbue_common.pure import pure
from loguru import logger
from pydantic import ValidationError

from app_instances.data_types import (
    CreateRequest,
    InstanceRecord,
    LocationRequest,
    RenameRequest,
)
from app_instances.errors import (
    AppInstancesError,
    InstanceConflictError,
    InvalidInstanceValueError,
    InvalidParamsError,
    LocationNotTrackedError,
    MalformedRequestError,
    NotReadyError,
    NotRenameableError,
    NotStoppableError,
    SearchNotSupportedError,
    UnknownActionError,
    UnknownInstanceError,
)
from app_instances.interfaces import InstanceNudgerInterface, InstanceSourceInterface
from app_instances.primitives import InstanceKey, SearchQuery

INSTANCES_PATH: Final[str] = "/_instances"
# The optional search capability sits under the instances path rather than beside it, so an app
# that mounts the blueprint under a prefix carries both together.
SEARCH_PATH: Final[str] = f"{INSTANCES_PATH}/search"

BLUEPRINT_NAME: Final[str] = "app_instances"

# How many matches the shell is given when it asks for no particular number, and the ceiling on
# what it may ask for: a result list is read at a glance, and a search runs on every keystroke.
DEFAULT_MATCH_COUNT: Final[int] = 10
MAX_MATCH_COUNT: Final[int] = 50

HTTP_OK: Final[int] = 200
HTTP_CREATED: Final[int] = 201
HTTP_NO_CONTENT: Final[int] = 204
HTTP_BAD_REQUEST: Final[int] = 400
HTTP_NOT_FOUND: Final[int] = 404
HTTP_CONFLICT: Final[int] = 409
HTTP_INTERNAL_ERROR: Final[int] = 500
HTTP_SERVICE_UNAVAILABLE: Final[int] = 503

_RequestModel = TypeVar("_RequestModel", bound=FrozenModel)


@pure
def status_code_for_error(error: AppInstancesError) -> int:
    """The HTTP status of contracts.md section 4.2 for each typed error; an unmapped library error is 500."""
    match error:
        case (
            MalformedRequestError()
            | InvalidInstanceValueError()
            | UnknownActionError()
            | InvalidParamsError()
            | NotRenameableError()
            | NotStoppableError()
            | LocationNotTrackedError()
            | SearchNotSupportedError()
        ):
            return HTTP_BAD_REQUEST
        case UnknownInstanceError():
            return HTTP_NOT_FOUND
        case InstanceConflictError():
            return HTTP_CONFLICT
        case NotReadyError():
            return HTTP_SERVICE_UNAVAILABLE
        case _:
            return HTTP_INTERNAL_ERROR


@pure
def _record_json(record: InstanceRecord) -> dict[str, Any]:
    return record.model_dump(mode="json")


def _parse_key(raw_key: str) -> InstanceKey:
    """The key of a keyed route, checked against the key rule before any source is consulted."""
    return InstanceKey(raw_key)


def _requested_match_count() -> int:
    """How many matches the search asked for, held to the route's ceiling; a bad number is a 400."""
    raw_limit = request.args.get("limit")
    if raw_limit is None:
        return DEFAULT_MATCH_COUNT
    try:
        requested = int(raw_limit)
    except ValueError as e:
        raise MalformedRequestError(f"invalid limit {raw_limit!r}: expected a whole number") from e
    if requested < 1:
        raise MalformedRequestError(f"invalid limit {requested}: expected at least 1")
    return min(requested, MAX_MATCH_COUNT)


def parse_request_body(model: type[_RequestModel]) -> _RequestModel:
    """The current request's body as ``model``; a body that is not a JSON object or not the shape raises MalformedRequestError (a 400).

    Every route an app serves beside the blueprint reads its body through here too, so one
    parse and one error shape cover the whole app.
    """
    # force=True: the shell and curl alike may post without a JSON content type.
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        raise MalformedRequestError("the request body must be a JSON object")
    try:
        return model.model_validate(body)
    except ValidationError as e:
        raise MalformedRequestError(describe_validation_error(e)) from e


def answer_typed_error(error: AppInstancesError) -> ResponseReturnValue:
    """The error handler for every library error: the status of contracts.md section 4.2 with a ``{"detail"}`` body.

    Registered on the blueprint for ``AppInstancesError``; an app's own blueprint registers it
    too, so its routes answer the app's errors (subclasses of the library's) the same way.
    """
    status_code = status_code_for_error(error)
    if status_code == HTTP_INTERNAL_ERROR:
        logger.opt(exception=error).error("Failed to serve an instances request")
    return jsonify({"detail": str(error)}), status_code


def build_instances_blueprint(
    source: InstanceSourceInterface, nudger: InstanceNudgerInterface
) -> Blueprint:
    """The instances API of contracts.md section 4.2 over ``source``, nudging ``nudger`` after every mutation.

    Mount it on the app's own Flask app, or let the sidecar serve it alone. Reads never nudge;
    every mutating route calls the source, then nudges, then answers.
    """
    blueprint = Blueprint(BLUEPRINT_NAME, __name__)

    @blueprint.get(INSTANCES_PATH)
    def list_instances() -> ResponseReturnValue:
        records = source.list_instances()
        return jsonify({"instances": [_record_json(record) for record in records]})

    @blueprint.get(SEARCH_PATH)
    def search_instances() -> ResponseReturnValue:
        query = SearchQuery(request.args.get("q", ""))
        matches = source.search_instances(query, _requested_match_count())
        return jsonify({"matches": [match.model_dump(mode="json") for match in matches]})

    @blueprint.post(INSTANCES_PATH)
    def create_instance() -> ResponseReturnValue:
        create_request = parse_request_body(CreateRequest)
        record = source.create_instance(create_request.action, create_request.params)
        nudger.nudge()
        return jsonify({"instance": _record_json(record)}), HTTP_CREATED

    @blueprint.delete(f"{INSTANCES_PATH}/<key>")
    def delete_instance(key: str) -> ResponseReturnValue:
        source.delete_instance(_parse_key(key))
        nudger.nudge()
        return "", HTTP_NO_CONTENT

    @blueprint.post(f"{INSTANCES_PATH}/<key>/rename")
    def rename_instance(key: str) -> ResponseReturnValue:
        instance_key = _parse_key(key)
        rename_request = parse_request_body(RenameRequest)
        record = source.rename_instance(instance_key, rename_request.title)
        nudger.nudge()
        return jsonify({"instance": _record_json(record)}), HTTP_OK

    @blueprint.post(f"{INSTANCES_PATH}/<key>/location")
    def set_location(key: str) -> ResponseReturnValue:
        instance_key = _parse_key(key)
        location_request = parse_request_body(LocationRequest)
        record = source.set_location(instance_key, location_request.path)
        nudger.nudge()
        return jsonify({"instance": _record_json(record)}), HTTP_OK

    @blueprint.post(f"{INSTANCES_PATH}/<key>/stop")
    def stop_instance(key: str) -> ResponseReturnValue:
        record = source.stop_instance(_parse_key(key))
        nudger.nudge()
        return jsonify({"instance": _record_json(record)}), HTTP_OK

    @blueprint.post(f"{INSTANCES_PATH}/<key>/start")
    def start_instance(key: str) -> ResponseReturnValue:
        record = source.start_instance(_parse_key(key))
        nudger.nudge()
        return jsonify({"instance": _record_json(record)}), HTTP_OK

    blueprint.register_error_handler(AppInstancesError, answer_typed_error)
    return blueprint


def build_instances_app(
    source: InstanceSourceInterface, nudger: InstanceNudgerInterface
) -> Flask:
    """A Flask app that serves nothing but the instances blueprint (what the sidecar and the stub app run)."""
    # No static folder: Flask would otherwise add a /static/<path> route beside the contract's routes.
    app = Flask(__name__, static_folder=None)
    app.register_blueprint(build_instances_blueprint(source, nudger))
    return app
