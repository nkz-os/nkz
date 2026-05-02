# N8N Workflows — Nekazari Platform

## Crop Health Alerts → LLM Analysis → Zulip Message

### Workflow
`n8n-workflow-crop-health-alerts.json`

### Flow
1. **Webhook trigger** receives crop events from risk-orchestrator
2. **Parse Event** extracts event data into structured context
3. **Filter** only processes HIGH/CRITICAL events
4. **LLM Prompt** builds an agronomic analysis prompt in Spanish
5. **Claude/OpenAI** analyzes the event and generates recommendations
6. **Zulip** sends the analysis as a message to `tenant-{id}-alerts` stream

### Setup

1. Import `n8n-workflow-crop-health-alerts.json` into N8N:
   - Open N8N at `https://n8n.nekazari.robotika.cloud`
   - Settings → Import from file
   - Select the JSON file

2. Configure credentials in N8N (Credentials → New):
   - **DeepSeek API** (preferred): API key from platform.deepseek.com. Add as "DeepSeek API" credential.
     If using Anthropic Claude instead: add API key from console.anthropic.com as "Anthropic API" credential.
   - **Zulip Bot** (HTTP Basic Auth):
     Username: `kubectl get secret zulip-secret -n nekazari -o jsonpath='{.data.bot-email}' | base64 -d`
     Password: `kubectl get secret zulip-secret -n nekazari -o jsonpath='{.data.bot-api-key}' | base64 -d`

3. Update the LLM node in the workflow:
   - Open the "LLM — Anthropic Claude" node
   - If using DeepSeek: change provider to DeepSeek, select DeepSeek credential
   - Model: `deepseek-chat` or `claude-sonnet-4-20250514`
   - Verify the prompt is in Spanish and agronomic context is preserved

4. Verify Zulip stream exists:
   ```bash
   kubectl exec -n nekazari deploy/zulip-provisioner -- python -c "
   from zulip_client import ZulipClient
   c = ZulipClient()
   c.ensure_stream('tenant-<TENANT_ID>-alerts')
   "
   ```

5. Register webhook in `tenant_risk_webhooks`:
   ```sql
   INSERT INTO tenant_risk_webhooks (tenant_id, name, url, events, min_severity, is_active)
   VALUES ('<TENANT_ID>', 'N8N Crop Alert Analyzer',
           'https://n8n.nekazari.robotika.cloud/webhook/crop-alert',
           ARRAY['crop_water_stress', 'risk_evaluation'], 'medium', true);
   ```

6. Activate the workflow (toggle ON in N8N)

### Testing

```bash
# Publish a test event to Redis
redis-cli XADD crop:events '*' payload '{"event_type":"crop.stress.breach","tenant_id":"test","parcel_id":"TestFarm-01","overall_severity":"HIGH","cwsi":0.72,"mds_severity":"HIGH","water_balance_mm":-3.2,"recommended_action":"IRRIGATE_SCHEDULED"}'

# Verify Zulip message appears in tenant-{id}-alerts stream > Crop Health topic
```
