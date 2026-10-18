"""
Vanna Agents - A modular framework for building LLM agents.

This package provides a flexible framework for creating conversational AI agents
with tool execution, conversation management, and user scoping.
"""

# Version information
__version__ = "0.1.0"

# Import core framework components
from .core import (
    # Interfaces
    Agent,
    ConversationStore,
    LlmService,
    SystemPromptBuilder,
    Tool,
    UserService,
    T,
    # Models
    Conversation,
    LlmMessage,
    LlmRequest,
    LlmResponse,
    LlmStreamChunk,
    Message,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSchema,
    User,
    # UI Components
    UiComponent,
    SimpleComponent,
    SimpleComponentType,
    SimpleTextComponent,
    SimpleImageComponent,
    SimpleLinkComponent,
    # Rich Components
    ArtifactComponent,
    BadgeComponent,
    CardComponent,
    DataFrameComponent,
    IconTextComponent,
    LogViewerComponent,
    NotificationComponent,
    ProgressBarComponent,
    ProgressDisplayComponent,
    RichTextComponent,
    StatusCardComponent,
    TaskListComponent,
    # Core implementations
    Agent,
    AgentConfig,
    DefaultSystemPromptBuilder,
    DefaultWorkflowHandler,
    ToolRegistry,
    # Context window management
    SlidingWindowFilter,
    SummaryBufferFilter,
    # Evaluation
    Evaluator,
    TestCase,
    ExpectedOutcome,
    AgentResult,
    EvaluationResult,
    TestCaseResult,
    AgentVariant,
    EvaluationRunner,
    TrajectoryEvaluator,
    OutputEvaluator,
    LLMAsJudgeEvaluator,
    EfficiencyEvaluator,
    EvaluationReport,
    ComparisonReport,
    EvaluationDataset,
    # Exceptions
    AgentError,
    ConversationNotFoundError,
    LlmServiceError,
    PermissionError,
    ToolExecutionError,
    ToolNotFoundError,
    ValidationError,
)

# Import basic implementations
from .integrations import (
    MemoryConversationStore,
    MockLlmService,
    SQLiteAgentMemory,
    SQLiteConversationStore,
)

# Storage configuration & backend factory
from .config import (
    StorageConfig,
    ObservabilityConfig,
    apply_storage_config,
    apply_observability_config,
    create_agent_memory,
    create_conversation_store,
    create_observability_provider,
    create_stores,
)

# Main exports
__all__ = [
    # Version
    "__version__",
    # Core interfaces
    "Agent",
    "Tool",
    "LlmService",
    "ConversationStore",
    "UserService",
    "SystemPromptBuilder",
    "T",
    # Models
    "User",
    "Message",
    "Conversation",
    "ToolCall",
    "ToolResult",
    "ToolContext",
    "ToolSchema",
    "LlmMessage",
    "LlmRequest",
    "LlmResponse",
    "LlmStreamChunk",
    # UI Components
    "UiComponent",
    "SimpleComponent",
    "SimpleComponentType",
    "SimpleTextComponent",
    "SimpleImageComponent",
    "SimpleLinkComponent",
    # Rich Components
    "ArtifactComponent",
    "BadgeComponent",
    "CardComponent",
    "DataFrameComponent",
    "IconTextComponent",
    "LogViewerComponent",
    "NotificationComponent",
    "ProgressBarComponent",
    "ProgressDisplayComponent",
    "RichTextComponent",
    "StatusCardComponent",
    "TaskListComponent",
    # Core implementations
    "Agent",
    "AgentConfig",
    "ToolRegistry",
    "DefaultSystemPromptBuilder",
    "DefaultWorkflowHandler",
    "SlidingWindowFilter",
    "SummaryBufferFilter",
    # Evaluation
    "Evaluator",
    "TestCase",
    "ExpectedOutcome",
    "AgentResult",
    "EvaluationResult",
    "TestCaseResult",
    "AgentVariant",
    "EvaluationRunner",
    "TrajectoryEvaluator",
    "OutputEvaluator",
    "LLMAsJudgeEvaluator",
    "EfficiencyEvaluator",
    "EvaluationReport",
    "ComparisonReport",
    "EvaluationDataset",
    # Basic implementations
    "MemoryConversationStore",
    "MockLlmService",
    # Persistent, multi-tenant-aware backends
    "SQLiteConversationStore",
    "SQLiteAgentMemory",
    # Storage configuration & factory
    "StorageConfig",
    "create_stores",
    "create_conversation_store",
    "create_agent_memory",
    "apply_storage_config",
    # Observability configuration & factory
    "ObservabilityConfig",
    "create_observability_provider",
    "apply_observability_config",
    # Server components
    "VannaFlaskServer",
    "VannaFastAPIServer",
    "ChatHandler",
    "ChatRequest",
    "ChatStreamChunk",
    "ExampleAgentLoader",
    # Exceptions
    "AgentError",
    "ToolExecutionError",
    "ToolNotFoundError",
    "PermissionError",
    "ConversationNotFoundError",
    "LlmServiceError",
    "ValidationError",
]
