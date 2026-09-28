# Store Listings

Tools for managing app store listings across languages.

!!! info "Text Limits"
    | Field | Max Length |
    |---|---|
    | Title | 30 characters |
    | Short description | 80 characters |
    | Full description | 4,000 characters |

    Use [`validate_listing_text`](validation.md#validate_listing_text) to check lengths before updating.

---

## get_listing

Get the store listing for a specific language.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `package_name` | string | Yes | — | App package name |
| `language` | string | No | `en-US` | Language code (e.g., `en-US`, `es-ES`, `fr-FR`) |

Returns: `language`, `title`, `short_description`, `full_description`, `video`

```python
get_listing("com.example.myapp", language="es-ES")
```

---

## update_listing

Update the store listing for a specific language. Only provided fields are updated.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `package_name` | string | Yes | App package name |
| `language` | string | Yes | Language code |
| `title` | string | No | App title (max 30 chars) |
| `full_description` | string | No | Full description (max 4,000 chars) |
| `short_description` | string | No | Short description (max 80 chars) |
| `video` | string | No | YouTube video URL |

```python
update_listing(
    package_name="com.example.myapp",
    language="en-US",
    title="My Awesome App",
    short_description="The best app for productivity",
    full_description="A comprehensive productivity app that helps you..."
)
```

---

## batch_update_listings

Validate or update store listings for multiple languages. This tool is a dry-run by default:
it validates all requested text locally and does not create a Google Play edit unless
`commit=True` is explicitly set.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `package_name` | string | Yes | — | App package name |
| `updates` | list | Yes | — | Listing updates by language |
| `commit` | boolean | No | `False` | Commit changes to Google Play when true |

Each `updates` item must include `language` and may include any of `title`,
`short_description`, `full_description`, and `video`.

```python
batch_update_listings(
    package_name="com.example.myapp",
    updates=[
        {"language": "en-US", "title": "My App", "short_description": "A better app"},
        {"language": "es-ES", "title": "Mi App"},
    ],
)
```

Set `commit=True` only after reviewing a successful dry-run result.

---

## list_all_listings

List store listings for all configured languages.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `package_name` | string | Yes | App package name |

```python
list_all_listings("com.example.myapp")
```

Returns a list of listings for every language configured in the Play Console.

---

## get_experiment_report_raw

Read one store listing experiment report. This is not an Android Publisher call. `gpcli experiment-report get` POSTs `storelistingexperiments/report:startupData` from the logged-in OpenCLI Play Console page.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `package_name` | string | Yes | App package name (display only) |
| `developer_id` | string | Yes | Numeric developer id from the Play Console URL |
| `app_id` | string | Yes | Numeric app id from the Play Console URL |
| `experiment_id` | string | Yes | Numeric experiment id. A non-numeric value errors before the RPC |

Request body: `{"6":{"1":{"1":"<developer_id>"},"2":{"1":"<app_id>"},"3":{"1":"<experiment_id>"}}}`.

Returns `experiment_id`, `image_urls`, `text_strings`, plus `name`, `status_code`, `start_timestamp`, `dimension_type`, and `variants` (`name`, `audience_percent`). Checked on the details page `store-listings/0/experiments/<id>/details` (2026-09-28, experiment 9063393730453672176):

| Output | Proto field | UI text |
|---|---|---|
| `name` | metadata 2 | heading `JJB-100-Android-71-position` |
| `status_code` | metadata 3, raw int | Status `Running` when the value is 1 |
| `start_timestamp` | metadata 7 | `Started on Sep 22, 2026` |
| `dimension_type` | metadata 4, raw int | Experiment type `Default graphics` when the value is 2 |
| `variants[].name` | result 3.3, or `Current listing` when that field is absent | Variants table row label |
| `variants[].audience_percent` | result 3.4 fraction × 100, rounded | Audience column `34%` / `33%` / `33%` |

Install and performance cells on that page were `-`. Those numbers are not output. An unknown experiment id is HTTP 404. A wrong app id is HTTP 403. Either one exits non-zero and does not print an empty report.
