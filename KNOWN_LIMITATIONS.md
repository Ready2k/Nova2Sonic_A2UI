# Known Limitations

- **Nova 2 Sonic Not Provided**: We are using simulated speech-to-text (STT) heuristics or deterministic mocks on the server instead of real streaming AI voice models.
- **Mocked Mortgage Logic**: Real underwriting and product fetch logic is stubbed out to return exactly 2 static products.
- **No Persistence**: The WebSocket session state is lost on disconnect. No database is used.
