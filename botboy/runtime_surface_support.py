from __future__ import annotations


def build_help_response(bot) -> dict:
    help_text = """BotBoy v{version} - Available Commands:

  MEMORY
    remember <text>       Store a memory
    search <query>        Search memories (FTS5)
    forget <query>        Delete matching memories
    memories              List all memories
    memstats              Memory database statistics
    reflect stats         Reflection archive statistics
    reflect list [task]   Reflection entries (optional task filter)
    reflect latest <id>   Latest reflection for a task
    reflect record <id> <text>  Archive a task reflection

  SKILLS (built-in)
    calculate <expr>      Safe AST calculator (sin, cos, sqrt, pi, ...)
    hash [algo] <text>    Hash text (md5/sha1/sha256/sha512)
    base64 enc|dec <data> Base64 encode/decode
    convert <n> <f> <t>   Unit conversion (km->mi, kg->lb, c->f, ...)
    weather <city>        Current weather (wttr.in)
    web search <query>    DuckDuckGo instant answer
    file search <pattern> File search (home directory)
    system info           Platform and runtime info
    time / date           Current UTC and local time

  SCHEDULER  [Phase 0]
    schedule list         List scheduled tasks
    schedule add "<name>" "<cron>"  Add scheduled task
    schedule cancel <id>  Cancel a task

  HISTORY    [Phase 0]
    history               List recent commands
    history stats         Execution statistics
    history principal <id>  Filter by principal
    history request <id>    Filter by request/trace id
    history clear         Clear history

  TASKS      [Wave 8]
    task list            List recent lifecycle tasks
    task show <id>       Show task details
    task merge <id>      Show merge review details
    task merge queue [n] Show actionable merge review queue
    task merge set-policy <id> <policy>  Update configured merge resolution policy
    task merge resolve <id> <key> <source>  Override one merged key to a worker or child source
    task merge resolve-many <id> <key=source> [<key=source> ...]  Apply multiple overrides
    task merge resolve-all-by-source <id> <source> [<key> ...]  Point pending keys to one source
    task merge clear-resolution <id> <key>  Clear one merge override
    task merge clear-many <id> <key> [<key> ...]  Clear multiple overrides
    task merge apply-preset <id> <preset>  Apply a review preset
    task merge reapply <id>  Recompute the merge using configured policy and overrides
    task events <id>     Show task events
    task artifacts <id>  List task artifacts
    task children <id>   List child tasks
    task graph <id>      Show root/child task graph
    task blockers        Show blocked and approval-pending tasks
    task leases          Show delegated worker leases and stale recovery candidates
    task operator [n]    Operator workbench: queue, blockers, recoverable leases
    task resume <id>     Resume a waiting/failed task
    task cancel <id>     Cancel a task
    task recover <id>    Recover a stale delegated child lease (admin)
    task reassign <id> <worker>  Reassign a child task (admin)

  WORKERS
    worker list          Show fixed internal workers
    worker show <id>     Show worker details
    worker node list     Show registered worker nodes
    worker node register <node> <worker> [endpoint]  Register a worker node
    worker node heartbeat <node> [status]  Refresh a worker-node lease
    worker node drain <node> [reason]  Mark a worker node as draining
    worker lease queues  Show execution queues and active lease depth
    worker lease list [queue]  Show queue leases
    worker lease acquire <queue> <node> [task]  Acquire a queue lease
    worker lease renew <lease>  Renew a queue lease
    worker lease release <lease> [reason]  Release a queue lease
    worker-daemon         External poll-loop worker for the local task store

  ORCHESTRATION
    handoff <worker> <command>  Delegate to a worker inside the current task
    handoff suggest <text>  Recommend the best internal worker and plan
    handoff batch [--resolution-policy=<policy>] <worker:cmd> || <worker:cmd>  Delegate a bounded worker batch
    a2a list             List bounded A2A pilot adapters
    a2a suggest <text>   Suggest an A2A adapter for a task
    a2a dispatch <adapter> <json>  Dispatch a bounded in-process A2A payload

  AI (requires LLM)
    plan <task>           TriPlex adversarial planning
    think <question>      Parallel CoT + TriPlex reasoning

  SYSTEM
    status                System status overview
    performance           Timing metrics (p50/p95/p99)
    version               Show version
    skills                List all loaded skills
    skilllib [name]       Inspect the agent skill library
    skillroute <query>    Suggest agent skills for a query
    contracts [name]      Inspect skill/tool contracts
    evals [wave] [json|text]  Run an eval/replay baseline
    security remote-readiness [host]  Check remote/auth rollout readiness
    help                  This help message
    exit / quit           Exit interactive mode""".format(version=bot.VERSION)
    return {"success": True, "output": help_text, "type": "help"}


def build_status_response(bot) -> dict:
    lines = [f"BotBoy v{bot.VERSION} - Status"]
    lines.append(f"  Memory:    {'OK' if bot.memory else 'N/A'}")
    lines.append(f"  Skills:    {len(bot.skills.list_skills()) if bot.skills else 0} loaded")
    lines.append(f"  Cache:     {'ON' if bot.cache else 'OFF'}")
    lines.append(f"  Scheduler: {'running' if (bot.scheduler and bot.scheduler._running) else 'N/A'}")
    lines.append(f"  History:   {'ON' if bot.history else 'OFF'}")
    lines.append(f"  Trace:     {'ON' if bot.trace_store else 'OFF'}")
    lines.append(f"  Tasks:     {'ON' if bot.task_store else 'OFF'}")
    lines.append(f"  LLM:       {bot.llm.backend_name if bot.llm else 'disabled'}")
    if bot.llm:
        lines.append(f"  Reflection: {'ON' if bot.reflection else 'OFF'}")
        lines.append(f"  Planning:   {'ON' if bot.planner else 'OFF'}")
        lines.append(f"  ParaThink:  {'ON' if bot.parallel_thinker else 'OFF'}")
    if bot.cache:
        cache_stats = bot.cache.stats()
        lines.append(
            f"  Cache hit rate: {cache_stats.hit_rate:.0%} ({cache_stats.hits} hits / {cache_stats.misses} misses)"
        )
    if bot.memory:
        memory_stats = bot.memory.get_stats()
        engine_label = "hybrid" if bot._hybrid_memory else "simple"
        lines.append(f"  Memories stored: {memory_stats['total']} (engine={engine_label})")
    if bot.router:
        router_stats = bot.router.stats()
        lines.append(f"  Channel messages: {router_stats['total_messages']}")
    if bot.archetypes:
        archetype_stats = bot.archetypes.stats()
        lines.append(f"  Archetypes routed: {archetype_stats['total_commands_routed']}")
    if bot.trace_store:
        trace_summary = bot.trace_store.summary()
        lines.append(f"  Trace runs: {trace_summary['total_runs']} ({trace_summary['total_spans']} spans)")
    if bot.task_store:
        task_summary = bot.task_store.summary()
        lines.append(
            f"  Tasks tracked: {task_summary['total']} "
            f"(active={task_summary['active_count']}, waiting_approval={task_summary['waiting_approval_count']})"
        )
        node_summary = task_summary.get("worker_nodes", {})
        lines.append(
            f"  Worker nodes: {node_summary.get('node_count', 0)} "
            f"(healthy={node_summary.get('healthy_count', 0)}, draining={node_summary.get('draining_count', 0)})"
        )
    if bot.reflection_archive:
        reflection_stats = bot.get_reflection_memory_payload()
        lines.append(f"  Reflection entries: {reflection_stats.get('total_entries', 0)}")
    if bot.delegation_advisor:
        delegation_stats = bot.get_delegation_intelligence_payload()
        lines.append(f"  Delegation workers: {delegation_stats.get('worker_count', 0)}")
    if bot.a2a_pilot:
        a2a_stats = bot.get_a2a_pilot_payload()
        lines.append(f"  A2A adapters: {a2a_stats.get('total_adapters', 0)}")
    return {"success": True, "output": "\n".join(lines), "type": "status"}
