from src.comms.server.api import SdkAPI
import uvicorn
from prometheus_fastapi_instrumentator import Instrumentator # <-- 1. IMPORT

if __name__ == "__main__":
    
    # Your app instance is created
    app_instance = SdkAPI()
    
    # Get the underlying FastAPI app from your class
    app_to_run = app_instance.app

    # --- 2. ADD THESE LINES ---
    # Instrument the app to expose Prometheus metrics
    # This automatically adds the /metrics endpoint
    print("Instrumenting FastAPI app for Prometheus...")
    Instrumentator().instrument(app_to_run).expose(app_to_run)
    print("Instrumentation complete. /metrics endpoint is active.")
    # --------------------------

    # 3. Run the instrumented app
    uvicorn.run(app_to_run, host="0.0.0.0", port=4097)

# from src.comms.server.api import SdkAPI
# import uvicorn
# import argparse

# if __name__ == "__main__":
#     parser = argparse.ArgumentParser()
#     parser.add_argument("--port", type=int, default=4017, help="Port to run the API on")
#     args = parser.parse_args()

#     app_instance = SdkAPI()
#     uvicorn.run(app_instance.app, host="0.0.0.0", port=args.port)

