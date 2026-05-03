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

2. Configure credentials:
   - **Anthropic API**: add API key in N8N credentials
   - **Zulip Bot**: create HTTP Basic Auth credential with bot email + API key
     (from `kubectl get secret zulip-secret -n nekazari -o jsonpath='{.data.bot-email}' | base64 -d`)

3. Register webhook in `tenant_risk_webhooks`:
   ```sql
   INSERT INTO tenant_risk_webhooks (tenant_id, name, url, events, min_severity, is_active)
   VALUES ('<tenant_id>', 'N8N Crop Alert Analyzer',
           'https://n8n.nekazari.robotika.cloud/webhook/crop-alert',
           ARRAY['crop_water_stress', 'risk_evaluation'], 'medium', true);
   ```

4. Activate the workflow (toggle ON in N8N)

### Testing

```bash
# Publish a test event to Redis
redis-cli XADD crop:events '*' payload '{"event_type":"crop.stress.breach","tenant_id":"test","parcel_id":"TestFarm-01","overall_severity":"HIGH","cwsi":0.72,"mds_severity":"HIGH","water_balance_mm":-3.2,"recommended_action":"IRRIGATE_SCHEDULED"}'

# Verify Zulip message appears in tenant-{id}-alerts stream > Crop Health topic
```
