(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.BotBoyControlCenterRenderer = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function escHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function normalizeControlValue(value, fallback = '—') {
    if (value === null || value === undefined || value === '') return fallback;
    if (Array.isArray(value)) {
      return value.length > 0
        ? value.map(item => normalizeControlValue(item, fallback)).join(' | ')
        : fallback;
    }
    if (typeof value === 'object') {
      const entries = Object.entries(value);
      if (entries.length === 0) return fallback;
      return entries
        .map(([key, val]) => `${key}: ${normalizeControlValue(val, fallback)}`)
        .join(' | ');
    }
    return String(value);
  }

  function controlTone(value) {
    if (typeof value === 'boolean') {
      return value ? 'good' : 'bad';
    }
    const text = String(value ?? '').trim().toLowerCase();
    if (!text || text === '—' || text === 'unknown' || text === 'none' || text === 'n/a') return 'muted';
    if (['verified', 'healthy', 'online', 'enabled', 'available', 'ready', 'ok', 'success', 'passed', 'operational'].includes(text)) return 'good';
    if (['experimental', 'pending', 'partial', 'degraded', 'warn', 'warning', 'watch', 'attention', 'needs-review', 'review-required', 'override-required', 'actionable'].includes(text)) return 'warn';
    if (['offline', 'failed', 'error', 'unavailable', 'disabled'].includes(text)) return 'bad';
    return 'muted';
  }

  function renderControlLine(label, value) {
    return `<div class="control-line"><span>${escHtml(label)}</span><strong>${escHtml(normalizeControlValue(value))}</strong></div>`;
  }

  function renderControlPill(label, value, tone = 'muted') {
    return `<span class="control-pill ${tone}"><span>${escHtml(label)}</span><strong>${escHtml(normalizeControlValue(value))}</strong></span>`;
  }

  function renderControlChip(label, value, tone = 'muted') {
    return `<span class="control-chip ${tone}">${escHtml(label)}${value !== undefined ? ': ' + escHtml(normalizeControlValue(value)) : ''}</span>`;
  }

  function summarizeList(list, limit = 3, fallback = 'none') {
    if (!Array.isArray(list) || list.length === 0) return fallback;
    return list.slice(0, limit).map(item => normalizeControlValue(item)).join(' | ');
  }

  function summarizeObject(obj, limit = 4, fallback = 'none') {
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return fallback;
    const entries = Object.entries(obj);
    if (entries.length === 0) return fallback;
    return entries.slice(0, limit).map(([key, val]) => `${key}: ${normalizeControlValue(val)}`).join(' | ');
  }

  function formatControlTimestamp(value) {
    const ts = Number(value);
    if (!Number.isFinite(ts)) return normalizeControlValue(value);
    return new Date(ts * 1000).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  }

  function renderObjectChips(obj, empty = 'none', limit = 4) {
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
      return `<span class="control-empty">${escHtml(empty)}</span>`;
    }
    const entries = Object.entries(obj);
    if (entries.length === 0) {
      return `<span class="control-empty">${escHtml(empty)}</span>`;
    }
    return entries
      .sort((a, b) => Number(b[1]) - Number(a[1]))
      .slice(0, limit)
      .map(([key, value]) => renderControlChip(key, value, controlTone(value)))
      .join('');
  }

  function renderRecentCommand(entry) {
    if (!entry || typeof entry !== 'object') {
      return `<div class="control-event">${escHtml(normalizeControlValue(entry))}</div>`;
    }
    const headline = [
      entry.cmd_type || 'command',
      entry.success === false ? 'failed' : 'ok',
    ].filter(Boolean).join(' · ');
    const details = [
      entry.principal ? `principal: ${entry.principal}` : '',
      entry.request_id ? `request: ${entry.request_id}` : '',
      entry.duration_s != null ? `${Math.round(Number(entry.duration_s) * 1000)}ms` : '',
      entry.timestamp != null ? formatControlTimestamp(entry.timestamp) : '',
    ].filter(Boolean).join(' | ');
    return `<div class="control-event"><strong>${escHtml(headline)}</strong><small>${escHtml(details || 'no additional context')}</small></div>`;
  }

  function renderRecentCommandList(entries, fallback = 'No recent commands recorded.') {
    if (!Array.isArray(entries) || entries.length === 0) {
      return `<div class="control-empty">${escHtml(fallback)}</div>`;
    }
    return `<div class="control-event-list">${entries.slice(-3).reverse().map(renderRecentCommand).join('')}</div>`;
  }

  function extractMergeReview(entry) {
    if (!entry || typeof entry !== 'object') return {};
    if (entry.merge && typeof entry.merge === 'object') return entry.merge;
    const result = entry.result && typeof entry.result === 'object' ? entry.result : {};
    if (result.merge && typeof result.merge === 'object') return result.merge;
    const resultData = result.data && typeof result.data === 'object' ? result.data : {};
    if (resultData.merge && typeof resultData.merge === 'object') return resultData.merge;
    const payload = entry.payload && typeof entry.payload === 'object' ? entry.payload : {};
    if (payload.merge && typeof payload.merge === 'object') return payload.merge;
    return {};
  }

  function deriveMergeReviewSignals(mergeReview, mergeResolution = {}) {
    const configuredPolicy = String(
      mergeReview.configured_resolution_policy ||
      mergeReview.configured_policy ||
      mergeResolution.configured_resolution_policy ||
      mergeResolution.configured_policy ||
      ''
    ).trim();
    const effectivePolicy = String(
      mergeReview.resolution_policy ||
      mergeReview.effective_resolution_policy ||
      mergeResolution.policy ||
      ''
    ).trim();
    const conflictCount = Number(mergeResolution.conflict_count ?? mergeReview.conflict_count ?? 0) || 0;
    const pendingChildren = Array.isArray(mergeReview.pending_child_ids) ? mergeReview.pending_child_ids.length : 0;
    const activeChildren = Number(mergeReview.active_child_count ?? 0) || 0;
    const overrideActive = Boolean(
      mergeReview.override_active ??
      mergeReview.merge_override_active ??
      (configuredPolicy && effectivePolicy && configuredPolicy !== effectivePolicy)
    );
    const actionable = Boolean(
      mergeReview.actionable ??
      mergeReview.merge_review_actionable ??
      (conflictCount > 0 || pendingChildren > 0 || activeChildren > 0 || overrideActive)
    );
    const reviewState = String(
      mergeReview.review_state ||
      mergeResolution.review_state ||
      (actionable ? 'needs-review' : 'ready')
    ).trim();
    const policyDelta = String(
      mergeReview.policy_delta ||
      mergeReview.merge_policy_delta ||
      (overrideActive && configuredPolicy && effectivePolicy ? `${configuredPolicy} -> ${effectivePolicy}` : '')
    ).trim();
    const overrideReason = String(
      mergeReview.override_reason ||
      mergeReview.merge_override_reason ||
      (overrideActive && configuredPolicy && effectivePolicy
        ? `configured ${configuredPolicy} -> effective ${effectivePolicy}`
        : '')
    ).trim();
    const nextAction = String(
      mergeReview.next_action ||
      mergeReview.merge_next_action ||
      (overrideActive
        ? 'Confirm override policy'
        : conflictCount > 0
          ? 'Resolve conflicts'
          : pendingChildren > 0 || activeChildren > 0
            ? 'Wait for child completion'
            : 'No action required')
    ).trim();
    return {
      configuredPolicy,
      effectivePolicy,
      conflictCount,
      pendingChildren,
      activeChildren,
      overrideActive,
      actionable,
      reviewState,
      policyDelta,
      overrideReason,
      nextAction,
    };
  }

  function renderValueChips(label, values, tone = 'muted', empty = 'none') {
    if (!Array.isArray(values) || values.length === 0) {
      return `<span class="control-empty">${escHtml(empty)}</span>`;
    }
    return values.slice(0, 5).map((value) => renderControlChip(label, value, tone)).join('');
  }

  function renderControlCenter(d) {
    const health = d.health || {};
    const metrics = d.metrics || {};
    const history = d.history || {};
    const traces = d.traces || {};
    const monitoring = d.monitoring || {};
    const readiness = d.system_readiness || {};
    const operationsSummary = d.operations_summary || {};
    const snapshot = d.status_snapshot || {};
    const verified = snapshot.verified_test_result || {};
    const lastRun = snapshot.last_verified_run || {};
    const gaps = Array.isArray(d.known_gaps) ? d.known_gaps : [];
    const tracks = snapshot.tracks || {};
    const gatewayModes = snapshot.gateway_modes || {};
    const authStatus = snapshot.auth || {};
    const traceStatus = snapshot.trace_status || {};
    const uiStatus = snapshot.ui_status || {};
    const evalReplay = snapshot.eval_replay || {};
    const openPriorities = Array.isArray(snapshot.open_priorities) ? snapshot.open_priorities : [];
    const completedCapabilities = Array.isArray(snapshot.completed_capabilities) ? snapshot.completed_capabilities : [];
    const deferredItems = Array.isArray(snapshot.deferred_items) ? snapshot.deferred_items : [];
    const tests = snapshot.tests || {};
    const traceCoverage = Array.isArray(traceStatus.coverage) ? traceStatus.coverage : [];
    const traceStatuses = Object.entries(traces.by_status || {});
    const recentCommands = Array.isArray(metrics.recent_commands) ? metrics.recent_commands : [];
    const lastCommand = metrics.last_command || (recentCommands.length > 0 ? recentCommands[recentCommands.length - 1] : null);
    const principalCounts = metrics.by_principal || history.by_principal || {};
    const trackEntries = Object.entries(tracks);
    const summaryTone = controlTone(operationsSummary.overall_status || '');
    const overallTone = summaryTone !== 'muted'
      ? summaryTone
      : Object.values(readiness).some(v => controlTone(v) === 'bad')
        ? 'bad'
        : Object.values(readiness).some(v => controlTone(v) === 'warn')
          ? 'warn'
          : 'good';
    const overallLabel = operationsSummary.overall_status
      ? normalizeControlValue(operationsSummary.overall_status)
      : overallTone === 'good' ? 'Operational' : overallTone === 'warn' ? 'Watch' : 'Attention';
    const overallDetail = overallTone === 'good'
      ? 'Control center matches the current snapshot.'
      : overallTone === 'warn'
        ? 'The core path is up, but follow-up work remains.'
        : 'At least one readiness signal needs attention.';
    const traceRunCount = traces.total_runs ?? '—';
    const traceSpanCount = traces.total_spans ?? '—';
    const traceStatusText = traceStatuses.length > 0
      ? traceStatuses.map(([key, value]) => `${key}:${value}`).join(' | ')
      : 'none';
    const traceCoverageCount = traceCoverage.length;
    const lastVerifiedCommand = lastRun.command || snapshot.verified_test_command || 'unknown';
    const successRate = history.success_rate != null ? Math.round(history.success_rate * 100) + '%' : '—';
    const avgLatency = history.avg_latency_ms != null ? Math.round(history.avg_latency_ms) + 'ms' : '—';
    const totalCommands = metrics.total_commands ?? history.total ?? '—';
    const errorRate = metrics.total_errors != null && metrics.total_commands
      ? Math.round((metrics.total_errors / Math.max(1, metrics.total_commands)) * 100) + '%'
      : '—';
    const monitoringSummary = summarizeObject(monitoring.summary);
    const commandTrail = recentCommands.slice(-3).reverse();
    const verifiedSignals = operationsSummary.verified_signals ?? Object.values(readiness).filter(v => v === 'verified').length;
    const attentionSignals = Array.isArray(operationsSummary.attention_signals)
      ? operationsSummary.attention_signals
      : [];
    const suiteStatus = operationsSummary.suite_status || verified.status || '—';
    const suiteRunText = operationsSummary.suite_ran != null
      ? `${operationsSummary.suite_ran} ran / ${operationsSummary.suite_skipped ?? 0} skipped`
      : verified.ran != null
        ? `${verified.ran} ran / ${verified.skipped ?? 0} skipped`
        : '—';
    const currentWave = operationsSummary.current_wave || snapshot.current_wave || '—';
    const latestTraceStatus = operationsSummary.latest_trace_status || traceStatusText;
    const latestTraceRunId = operationsSummary.latest_trace_run_id || '—';
    const metricsTotalCommands = operationsSummary.metrics_total_commands ?? totalCommands;
    const historyTotal = operationsSummary.history_total ?? history.total ?? '—';
    const mergeReviewReadyCount = operationsSummary.merge_review_ready_count ?? 0;
    const mergeConflictTaskCount = operationsSummary.merge_conflict_task_count ?? 0;
    const mergeResolutionPolicies = operationsSummary.merge_resolution_policies || {};
    const latestMergeResolutionPolicy = operationsSummary.latest_merge_resolution_policy || '—';
    const latestTask = d.tasks?.latest_task || d.tasks?.latest || null;
    const latestMergeReview = extractMergeReview(latestTask);
    const latestMergeResolution = latestMergeReview.merge_resolution && typeof latestMergeReview.merge_resolution === 'object'
      ? latestMergeReview.merge_resolution
      : {};
    const latestMergeSignals = deriveMergeReviewSignals(latestMergeReview, latestMergeResolution);
    const latestMergeAvailable = Boolean(
      latestMergeReview.merge_policy ||
      latestMergeReview.resolution_policy ||
      latestMergeResolution.policy
    );
    const latestMergeTaskLabel = latestTask?.task_id || operationsSummary.latest_merge_task_id || '—';
    const latestMergeSource = latestMergeReview.merged_from_child_task_id || latestMergeReview.merged_from_task_id || '—';
    const latestMergeWorker = latestMergeReview.merged_from_worker || '—';
    const latestMergeConflictCount = latestMergeResolution.conflict_count ?? latestMergeReview.conflict_count ?? 0;
    const latestMergeLinkedArtifacts = latestMergeReview.linked_artifact_count ?? 0;
    const latestMergeCompletedChildren = latestMergeReview.completed_child_count ?? 0;
    const latestMergeActiveChildren = latestMergeReview.active_child_count ?? 0;
    const latestMergeWorkers = Array.isArray(latestMergeReview.workers_involved) ? latestMergeReview.workers_involved : [];
    const latestMergePendingChildren = Array.isArray(latestMergeReview.pending_child_ids) ? latestMergeReview.pending_child_ids : [];
    const latestMergeConfiguredPolicy = latestMergeSignals.configuredPolicy || '—';
    const latestMergeEffectivePolicy = latestMergeSignals.effectivePolicy || latestMergeResolutionPolicy || '—';
    const latestMergeActionable = latestMergeSignals.actionable;
    const latestMergeOverrideActive = latestMergeSignals.overrideActive;
    const latestMergeReviewState = latestMergeSignals.reviewState || '—';
    const latestMergePolicyDelta = latestMergeSignals.policyDelta || '—';
    const latestMergeNextAction = latestMergeSignals.nextAction || '—';
    const latestMergeResolvedKeys = Array.isArray(latestMergeResolution.resolved_keys)
      ? latestMergeResolution.resolved_keys
      : Array.isArray(latestMergeReview.resolved_keys)
        ? latestMergeReview.resolved_keys
        : [];

    return `
      <div class="control-banner" data-smoke="control-banner">
        <div class="control-banner-top">
          <div>
            <div class="control-title">Operations Control Center</div>
            <div class="control-subtitle">
              Canonical UI: ${escHtml(uiStatus.canonical_ui || 'web/index.html')} | Prototype: ${escHtml(uiStatus.dashboard_prototype || 'web/dashboard.html')}
            </div>
          </div>
          <div class="control-status ${overallTone}">
            ${escHtml(overallLabel)}
            <small>${escHtml(overallDetail)}</small>
          </div>
        </div>
        <div class="control-pill-row">
          ${renderControlPill('Mode', health.mode || '—', controlTone(health.mode))}
          ${renderControlPill('Readiness', overallLabel, overallTone)}
          ${renderControlPill('Version', health.version || '—', 'muted')}
          ${renderControlPill('Trace Runs', traceRunCount, traceRunCount !== '—' ? 'good' : 'muted')}
          ${renderControlPill('Trace Coverage', traceCoverageCount, traceCoverageCount ? 'good' : 'muted')}
          ${renderControlPill('Verified Signals', verifiedSignals, verifiedSignals ? 'good' : 'muted')}
          ${renderControlPill('Attention Signals', attentionSignals.length, attentionSignals.length ? 'warn' : 'good')}
          ${renderControlPill('Current Wave', currentWave, 'muted')}
          ${renderControlPill('Last Verified', lastVerifiedCommand, 'muted')}
          ${renderControlPill('Known Gaps', gaps.length, gaps.length ? 'warn' : 'good')}
          ${renderControlPill('Completed', completedCapabilities.length, completedCapabilities.length ? 'good' : 'muted')}
          ${renderControlPill('Recent Commands', recentCommands.length, recentCommands.length ? 'good' : 'muted')}
        </div>
      </div>

      <div class="control-stack">
        <div class="control-card" data-smoke="operations-summary">
          <div class="control-card-title">Operations Summary</div>
          <div class="control-lines">
            ${renderControlLine('Overall status', overallLabel)}
            ${renderControlLine('Suite status', suiteStatus)}
            ${renderControlLine('Suite run', suiteRunText)}
            ${renderControlLine('Current wave', currentWave)}
            ${renderControlLine('Trace runs', operationsSummary.trace_runs ?? traceRunCount)}
            ${renderControlLine('Latest trace status', latestTraceStatus)}
            ${renderControlLine('Latest trace run', latestTraceRunId)}
            ${renderControlLine('Metrics commands', metricsTotalCommands)}
            ${renderControlLine('History total', historyTotal)}
            ${renderControlLine('Merge reviews', mergeReviewReadyCount)}
            ${renderControlLine('Merge conflict tasks', mergeConflictTaskCount)}
            ${renderControlLine('Merge policies', summarizeObject(mergeResolutionPolicies))}
            ${renderControlLine('Latest merge policy', latestMergeResolutionPolicy)}
            ${renderControlLine('Attention signals', summarizeList(attentionSignals, 4))}
          </div>
        </div>

        <div class="control-card" data-smoke="latest-merge-review">
          <div class="control-card-title">Latest Merge Review</div>
          <div class="control-lines">
            ${renderControlLine('Latest merge task', latestMergeTaskLabel)}
            ${renderControlLine('Available', latestMergeAvailable ? 'yes' : 'no')}
            ${renderControlLine('Merge policy', latestMergeReview.merge_policy || '—')}
            ${renderControlLine('Configured policy', latestMergeConfiguredPolicy)}
            ${renderControlLine('Resolution policy', latestMergeEffectivePolicy)}
            ${renderControlLine('Review state', latestMergeReviewState)}
            ${renderControlLine('Actionable', latestMergeActionable ? 'yes' : 'no')}
            ${renderControlLine('Override active', latestMergeOverrideActive ? 'yes' : 'no')}
            ${renderControlLine('Policy delta', latestMergePolicyDelta)}
            ${renderControlLine('Suggested next action', latestMergeNextAction)}
            ${renderControlLine('Source child', latestMergeSource)}
            ${renderControlLine('Merged by worker', latestMergeWorker)}
            ${renderControlLine('Conflict count', latestMergeConflictCount)}
            ${renderControlLine('Linked artifacts', latestMergeLinkedArtifacts)}
            ${renderControlLine('Completed children', latestMergeCompletedChildren)}
            ${renderControlLine('Active children', latestMergeActiveChildren)}
            ${renderControlLine('Workers involved', latestMergeWorkers.length)}
            ${renderControlLine('Resolved keys', latestMergeResolvedKeys.length)}
            ${renderControlLine('Pending children', latestMergePendingChildren.length)}
          </div>
          <div class="control-chip-list" style="margin-top:8px">
            ${renderValueChips('worker', latestMergeWorkers, 'good', 'No worker provenance reported.')}
          </div>
          <div class="control-chip-list" style="margin-top:6px">
            ${renderControlPill('Actionable', latestMergeActionable ? 'yes' : 'no', latestMergeActionable ? 'warn' : 'good')}
            ${renderControlPill('Override', latestMergeOverrideActive ? 'yes' : 'no', latestMergeOverrideActive ? 'warn' : 'good')}
            ${renderControlPill('Review state', latestMergeReviewState, controlTone(latestMergeReviewState))}
            ${renderControlPill('Policy delta', latestMergePolicyDelta, latestMergeOverrideActive ? 'warn' : 'good')}
          </div>
          <div class="control-chip-list" style="margin-top:6px">
            ${renderValueChips('resolved', latestMergeResolvedKeys, 'muted', 'No resolved keys reported.')}
          </div>
          <div class="control-chip-list" style="margin-top:6px">
            ${renderValueChips('pending', latestMergePendingChildren, 'warn', 'No pending children.')}
          </div>
        </div>

        <div class="control-card" data-smoke="readiness-matrix">
          <div class="control-card-title">Readiness Matrix</div>
          <div class="control-lines">
            ${Object.entries(readiness).length > 0
              ? Object.entries(readiness).map(([key, value]) => renderControlLine(key, value)).join('')
              : `<div class="control-empty">No readiness data available.</div>`}
          </div>
        </div>

        <div class="control-card" data-smoke="verification">
          <div class="control-card-title">Verification</div>
          <div class="control-lines">
            ${renderControlLine('Tests available', tests.available === true ? 'yes' : tests.available === false ? 'no' : 'unknown')}
            ${renderControlLine('Verified tests', verified.status || '—')}
            ${renderControlLine('Test run', verified.ran != null ? `${verified.ran} ran / ${verified.skipped ?? 0} skipped` : '—')}
            ${renderControlLine('Last verified', lastRun.command || snapshot.verified_test_command || 'unknown')}
            ${renderControlLine('Result', lastRun.result || 'n/a')}
          </div>
        </div>

        <div class="control-card" data-smoke="trace-monitoring">
          <div class="control-card-title">Trace and Monitoring</div>
          <div class="control-lines">
            ${renderControlLine('Trace store', traceStatus.store || (traceStatus.available ? 'available' : 'offline'))}
            ${renderControlLine('Trace coverage', summarizeList(traceCoverage, 4))}
            ${renderControlLine('Trace status', traceStatusText)}
            ${renderControlLine('Monitoring', monitoringSummary)}
            ${renderControlLine('History success', successRate)}
            ${renderControlLine('History latency', avgLatency)}
            ${renderControlLine('Commands', totalCommands)}
            ${renderControlLine('Error rate', errorRate)}
          </div>
        </div>

        <div class="control-card" data-smoke="command-context">
          <div class="control-card-title">Command Context</div>
          <div class="control-lines">
            ${renderControlLine('Gateway modes', summarizeObject(gatewayModes))}
            ${renderControlLine('UI status', summarizeObject(uiStatus))}
            ${renderControlLine('Eval replay', evalReplay.status || '—')}
            ${renderControlLine('Replay focus', summarizeList(evalReplay.focus, 3))}
            ${renderControlLine('Last command', lastCommand ? summarizeObject(lastCommand, 6) : 'none')}
            ${renderControlLine('Recent commands', recentCommands.length)}
          </div>
          <div class="control-chip-list" style="margin-top:8px">
            ${Object.entries(authStatus).length > 0
              ? Object.entries(authStatus).map(([key, value]) => renderControlChip(key, value, controlTone(value))).join('')
              : `<span class="control-empty">Auth contract not reported.</span>`}
          </div>
          <div class="control-event-list">
            ${renderRecentCommandList(commandTrail)}
          </div>
          <div class="control-chip-list" style="margin-top:8px">
            ${renderObjectChips(principalCounts, 'No principal data', 4)}
          </div>
        </div>

        <div class="control-card" data-smoke="delivery-snapshot">
          <div class="control-card-title">Delivery Snapshot</div>
          <div class="control-lines">
            ${renderControlLine('Current wave', snapshot.current_wave || '—')}
            ${renderControlLine('Open priorities', summarizeList(openPriorities, 3))}
            ${renderControlLine('Known gaps', summarizeList(gaps, 4))}
            ${renderControlLine('Deferred items', summarizeList(deferredItems, 3))}
            ${renderControlLine('Completed capabilities', completedCapabilities.length ? `${completedCapabilities.length} done` : 'none')}
          </div>
          <div class="control-chip-list" style="margin-top:8px">
            ${trackEntries.length > 0
              ? trackEntries.map(([key, value]) => renderControlChip(key, value, controlTone(value))).join('')
              : `<span class="control-empty">No track data available.</span>`}
          </div>
          <div class="control-chip-list" style="margin-top:6px">
            ${completedCapabilities.length > 0
              ? completedCapabilities.slice(0, 5).map(item => renderControlChip('done', item, 'good')).join('')
              : `<span class="control-empty">No completed capabilities reported.</span>`}
          </div>
          <div class="control-chip-list" style="margin-top:6px">
            ${deferredItems.length > 0
              ? deferredItems.slice(0, 4).map(item => renderControlChip('deferred', item, 'warn')).join('')
              : `<span class="control-empty">No deferred items reported.</span>`}
          </div>
          <div class="control-chip-list" style="margin-top:6px">
            ${openPriorities.length > 0
              ? openPriorities.slice(0, 4).map(item => renderControlChip('priority', item, 'muted')).join('')
              : `<span class="control-empty">No open priorities reported.</span>`}
          </div>
          <div class="control-chip-list" style="margin-top:6px">
            ${evalReplay.manifest ? renderControlChip('manifest', evalReplay.manifest, 'muted') : ''}
            ${evalReplay.replay_seed ? renderControlChip('seed', evalReplay.replay_seed, 'muted') : ''}
            ${traceSpanCount !== '—' ? renderControlChip('spans', traceSpanCount, 'muted') : ''}
          </div>
        </div>
      </div>
    `;
  }

  return {
    escHtml,
    normalizeControlValue,
    controlTone,
    renderControlLine,
    renderControlPill,
    renderControlChip,
    summarizeList,
    summarizeObject,
    formatControlTimestamp,
    renderObjectChips,
    renderRecentCommand,
    renderRecentCommandList,
    renderControlCenter,
  };
});
