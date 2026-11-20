"""OpenAPI 3 description of the HTTP API, served at GET /openapi.json.

It documents the contract for API users and lets security scanners (OWASP ZAP in CI)
discover and test every endpoint.
"""

_ERROR = {
    "type": "object",
    "required": ["error"],
    "properties": {"error": {"type": "string", "example": "Amount cannot be negative, got -5"}},
}
_NOTES = {
    "type": "object",
    "description": "denomination (as text) -> number of notes",
    "additionalProperties": {"type": "integer", "minimum": 0},
    "example": {"500": 3, "200": 9, "100": 20},
}


def _json(schema, description):
    return {"description": description, "content": {"application/json": {"schema": schema}}}


OPENAPI_SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "ATM Cash Dispenser API",
        "version": "1.0.0",
        "description": "Dispenses withdrawals with the fewest notes from a limited inventory.",
    },
    "components": {
        "securitySchemes": {"apiKey": {"type": "http", "scheme": "bearer"}},
        "schemas": {"Error": _ERROR, "Notes": _NOTES},
    },
    "paths": {
        "/health": {"get": {"summary": "Liveness probe", "security": [],
                            "responses": {"200": _json({"type": "object"}, "Service is alive")}}},
        "/ready": {"get": {"summary": "Readiness probe", "security": [],
                           "responses": {"200": _json({"type": "object"}, "Service is ready")}}},
        "/inventory": {"get": {
            "summary": "Notes currently in the ATM",
            "security": [{"apiKey": []}],
            "responses": {
                "200": _json({"type": "object", "properties": {"inventory": _NOTES,
                                                               "total_cash": {"type": "integer"}}},
                             "Current inventory"),
                "401": _json(_ERROR, "Missing or invalid API key (only when ATM_API_KEY is set)"),
            },
        }},
        "/withdrawals": {"post": {
            "summary": "Withdraw an amount",
            "security": [{"apiKey": []}],
            "requestBody": {"required": True, "content": {"application/json": {"schema": {
                "type": "object",
                "required": ["amount"],
                "additionalProperties": False,
                "properties": {"amount": {"type": "integer", "minimum": 1, "example": 2700}},
            }}}},
            "responses": {
                "200": _json({"type": "object", "properties": {
                    "amount": {"type": "integer"}, "dispensed": _NOTES,
                    "total_notes": {"type": "integer"}, "inventory": _NOTES}}, "Cash dispensed"),
                "400": _json(_ERROR, "Invalid amount or request body"),
                "401": _json(_ERROR, "Missing or invalid API key"),
                "413": _json(_ERROR, "Request body larger than 1 KB"),
                "415": _json(_ERROR, "Content-Type is not application/json"),
                "422": _json(_ERROR, "Cannot dispense requested amount (inventory unchanged)"),
            },
        }},
        "/openapi.json": {"get": {"summary": "This document", "security": [],
                                  "responses": {"200": _json({"type": "object"}, "OpenAPI document")}}},
    },
}
