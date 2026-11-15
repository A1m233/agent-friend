use super::{available_models, ready_providers, AvailableModel};
use crate::settings::{self, FirstModelDefaults, ModelSelection, Settings};
use std::time::Duration;
use tauri::{AppHandle, Manager};
use tokio::sync::{Mutex, Notify};

#[derive(Default)]
pub(crate) struct DefaultsState {
    observation: Mutex<()>,
    retry: Notify,
}

fn first_selection(settings: &Settings, models: &[AvailableModel]) -> Option<ModelSelection> {
    let selected = models
        .iter()
        .find(|m| {
            m.provider == settings.chat_model.provider && m.model == settings.chat_model.model
        })
        .or_else(|| models.first())?;
    Some(ModelSelection {
        provider: selected.provider,
        model: selected.model.clone(),
        reasoning_preset: None,
    })
}

fn baseline(settings: &Settings, builtin_ready: bool, custom: &[String]) -> FirstModelDefaults {
    let has_custom_models = settings.model_connections["connections"]
        .as_array()
        .into_iter()
        .flatten()
        .any(|c| {
            c["id"]
                .as_str()
                .is_some_and(|id| custom.iter().any(|ready| ready == id))
                && c["models"]
                    .as_array()
                    .is_some_and(|models| !models.is_empty())
        });
    if settings.onboarding_completed || builtin_ready || has_custom_models {
        FirstModelDefaults::Settled
    } else {
        FirstModelDefaults::AwaitingModel
    }
}

async fn record_baseline(app: &AppHandle, settings: &Settings) -> Result<(), String> {
    // Both built-ins have a bundled model catalog; no live bridge is needed to
    // distinguish an already configured installation from an empty one here.
    let (deepseek, kimi, custom) = ready_providers(app, settings).await?;
    settings::save_first_model_defaults(
        app,
        settings,
        baseline(settings, deepseek || kimi, &custom),
        None,
    )
}

async fn observe(app: &AppHandle) -> Result<(), String> {
    let state = app.state::<DefaultsState>();
    let _observation = state.observation.lock().await;
    let settings = settings::migration_snapshot(&app.state())?;
    match settings.first_model_defaults {
        FirstModelDefaults::Unchecked => record_baseline(app, &settings).await?,
        FirstModelDefaults::AwaitingModel => {
            let models = available_models(app, &settings).await?;
            if let Some(selection) = first_selection(&settings, &models) {
                settings::save_first_model_defaults(
                    app,
                    &settings,
                    FirstModelDefaults::Settled,
                    Some(selection),
                )?;
            }
        }
        FirstModelDefaults::Settled => {}
    }
    Ok(())
}

/// Capture the pre-change state even if a user saves before startup inspection finishes.
pub(crate) async fn before_change(app: &AppHandle) -> Result<(), String> {
    let state = app.state::<DefaultsState>();
    let _observation = state.observation.lock().await;
    let settings = settings::migration_snapshot(&app.state())?;
    if settings.first_model_defaults == FirstModelDefaults::Unchecked {
        record_baseline(app, &settings).await?;
    }
    Ok(())
}

pub(crate) async fn after_change(app: &AppHandle) {
    if let Err(e) = observe(app).await {
        log::warn!("first model defaults: {e}");
        app.state::<DefaultsState>().retry.notify_one();
    }
}

pub(crate) async fn recover(app: AppHandle) {
    let state = app.state::<DefaultsState>();
    loop {
        match observe(&app).await {
            Ok(()) => state.retry.notified().await,
            Err(e) => {
                log::warn!("first model defaults: {e}; retrying");
                tokio::time::sleep(Duration::from_secs(15)).await;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::ModelProvider;

    #[test]
    fn existing_models_are_preserved_but_empty_installations_wait_for_the_first_model() {
        use FirstModelDefaults::*;
        let mut settings = Settings::default();
        assert_eq!(baseline(&settings, false, &[]), AwaitingModel);
        assert_eq!(baseline(&settings, true, &[]), Settled);
        let ids = vec!["first".to_owned()];
        settings.model_connections =
            serde_json::json!({"connections":[{"id":"first","models":[]}]});
        assert_eq!(baseline(&settings, false, &ids), AwaitingModel);
        settings.model_connections["connections"][0]["models"] = serde_json::json!([{"id":"a"}]);
        assert_eq!(baseline(&settings, false, &[]), AwaitingModel);
        assert_eq!(baseline(&settings, false, &ids), Settled);
        settings.first_model_defaults = Settled;
        let stored = serde_json::to_value(&settings).unwrap();
        assert_eq!(
            serde_json::from_value::<Settings>(stored)
                .unwrap()
                .first_model_defaults,
            Settled
        );
    }

    #[test]
    fn uses_a_matching_default_or_the_first_available_catalog_entry() {
        let settings = Settings::default();
        let custom = AvailableModel {
            provider: ModelProvider::Custom,
            model: "custom/first/a".into(),
            label: "A".into(),
        };
        let matching = AvailableModel {
            provider: settings.chat_model.provider,
            model: settings.chat_model.model.clone(),
            label: "B".into(),
        };
        assert!(first_selection(&settings, &[]).is_none());
        assert_eq!(
            first_selection(&settings, std::slice::from_ref(&custom))
                .unwrap()
                .model,
            custom.model
        );
        assert_eq!(
            first_selection(&settings, &[custom, matching])
                .unwrap()
                .model,
            settings.chat_model.model
        );
    }
}
