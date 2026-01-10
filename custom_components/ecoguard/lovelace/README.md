# EcoGuard Dashboard

This directory contains a pre-configured Lovelace dashboard for the EcoGuard integration.

## Quick Start

### Option 1: Import Dashboard (Recommended)

1. In Home Assistant, go to **Settings** → **Dashboards**
2. Click the **three dots menu** (⋮) in the top right
3. Select **Import dashboard**
4. Navigate to `custom_components/ecoguard/lovelace/dashboard.yaml`
5. Select the file and click **Import**

The dashboard will be added to your Home Assistant instance with three views:
- **Overview**: Key metrics and trends
- **Daily Details**: Daily consumption and costs
- **Monthly Details**: Monthly totals and billing information

### Option 2: Manual Configuration

1. Copy the contents of `dashboard.yaml`
2. In Home Assistant, go to **Settings** → **Dashboards**
3. Click **Add Dashboard** → **Take control**
4. Click the **three dots menu** (⋮) → **Edit Dashboard**
5. Click the **three dots menu** (⋮) → **Raw configuration editor**
6. Paste the YAML content
7. Save

### Option 3: Add to Existing Dashboard

1. Open your existing dashboard
2. Click the **three dots menu** (⋮) → **Edit Dashboard**
3. Click **Add Card**
4. Select **Manual** or copy individual card configurations from `dashboard.yaml`

## Customization

After importing, you can customize the dashboard:

- **Edit cards**: Click on any card and select **Edit**
- **Add cards**: Click **Add Card** to add more sensors or visualizations
- **Reorder cards**: Drag and drop cards to reorder them
- **Change layout**: Use different card types (grid, vertical-stack, horizontal-stack)

## Entity IDs

The dashboard uses entity IDs based on the default sensor naming. If you've customized sensor names, you may need to update the entity IDs in the dashboard configuration.

**Entity ID Pattern:**
- Individual meter sensors include `_meter_`: `sensor.consumption_daily_metered_cold_water_meter_kaldtvann_bad`
- Aggregate/accumulated sensors do NOT include `_meter_`: `sensor.consumption_daily_metered_cold_water`
- See the main README.md for complete entity ID patterns

To find your entity IDs:
1. Go to **Settings** → **Devices & Services**
2. Select **EcoGuard**
3. Click **Entities**
4. Find the sensor you want and note its entity ID

## Troubleshooting

### Dashboard doesn't show data

- Verify that your sensors are enabled and have data
- Check that entity IDs in the dashboard match your actual sensor entity IDs
- Ensure the integration is properly configured and sensors are updating

### Missing sensors

- Some sensors may be disabled by default (individual meter sensors)
- Enable them in **Settings** → **Devices & Services** → **EcoGuard** → **Entities** if needed
- The dashboard focuses on aggregated sensors which are enabled by default

### Entity IDs don't match

- Entity IDs are generated from sensor names
- If you've customized sensor names, the entity IDs will be different
- Update the entity IDs in the dashboard configuration to match your sensors

## Dashboard Views

### Overview
- Daily summary (consumption and cost)
- Monthly cost gauge
- Consumption by utility type
- Cost breakdown
- Consumption and cost trends (7 days)

### Daily Details
- Daily consumption by utility
- Daily costs (metered and estimated)
- Consumption trends (30 days)

### Monthly Details
- Monthly consumption totals
- Monthly costs (metered and estimated)
- Billing information
- Total monthly cost gauge

## Advanced: Custom Lovelace Card

For a more advanced, interactive dashboard experience, you can develop a custom Lovelace card. See `DASHBOARD_GUIDE.md` in the repository root for more information.
