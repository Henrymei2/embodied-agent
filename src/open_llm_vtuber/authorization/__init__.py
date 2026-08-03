"""Central action authorization contracts and fail-closed policy kernel."""

from .contracts import (
    ACTION_DESCRIPTOR_SCHEMA_VERSION,
    AUTHORIZATION_POLICY_VERSION,
    ActionDescriptor,
    AuthorizationContext,
    AuthorizationDecision,
    AuthorizationValidationError,
    PolicyCheck,
    UserConfirmation,
    build_action_descriptor,
    hash_arguments,
)
from .kernel import AuthorizationKernel
from .journal import (
    ActionTransactionJournal,
    AuthorizationJournalError,
    get_action_transaction_journal,
)
from .profiles import ActionProfile, profile_for
from .transaction import ActionTransactionCoordinator, TransactionResult

__all__ = [
    "ACTION_DESCRIPTOR_SCHEMA_VERSION",
    "AUTHORIZATION_POLICY_VERSION",
    "ActionDescriptor",
    "AuthorizationContext",
    "AuthorizationDecision",
    "AuthorizationKernel",
    "ActionProfile",
    "ActionTransactionCoordinator",
    "ActionTransactionJournal",
    "AuthorizationJournalError",
    "AuthorizationValidationError",
    "PolicyCheck",
    "UserConfirmation",
    "TransactionResult",
    "build_action_descriptor",
    "hash_arguments",
    "get_action_transaction_journal",
    "profile_for",
]
