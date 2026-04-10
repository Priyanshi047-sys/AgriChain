#  AgriChain: Ensuring the data Integrity of IoT Sensor using blockchain technology in Agriculture sector

**AgriChain** is an end-to-end industrial IoT solution that captures real-time soil vitals (Moisture & Temperature) and secures them on an immutable blockchain ledger. This project demonstrates a complete data pipeline across four programming languages, ensuring data integrity from the physical sensor to the digital dashboard.


##  System Architecture

The project is built using a modular microservice architecture:

1.  **Hardware Layer (C++):** ESP32 NodeMCU 32s polls the Soil Moisture and Temperature sensors.
2.  **Bridge Layer (Python):** Acts as a middleware to ingest raw sensor data, handle serialization, and manage validation logic.
3.  **Integration Layer (Node.js):** Orchestrates the API requests and interfaces with the Blockchain SDK.
4.  **Ledger Layer (Go):** Smart Contracts (Chaincode) define the logic for data persistence on the blockchain.
5.  **Visualization Layer:** A real-time dashboard that provides data "stimulation"—visualizing environmental trends and triggering alerts.


## Tech Stack

| Component | Technology | Language |
| :--- | :--- | :--- |
| **Microcontroller** | ESP32 NodeMCU 32s | **C++** |
| **Backend Bridge** | Flask / Python Logic | **Python** |
| **API Middleware** | Node.js / Express | **JavaScript** |
| **Smart Contracts** | Hyperledger Fabric Chaincode | **Go (Golang)** |
| **Dashboard** | Web UI (React/HTML) | **JavaScript** |

---

##  Key Features
* **Immutable Records:** All sensor data is anchored to the blockchain using Go-based chaincode, making it tamper-proof.
* **Real-Time Stimulation:** The dashboard visualizes live sensor states, simulating soil conditions for immediate agricultural response.
* **Multi-Protocol Flow:** Demonstrates complex data handling across HTTP, MQTT, and Blockchain SDKs.
* **Security-First:** Uses environment variables to protect sensitive blockchain wallet credentials and API keys.

---

## 📂 Project Structure

```text
AgriProject/
├── temperature_sensor/ # ESP32 Firmware (C++)
├── python/             # Data ingestion & Validation (Python)
├── chaincode/          # Blockchain Smart Contracts (Go)
├── node_modules/       # Middleware dependencies (JS)
├── server.js           # Node.js API Gateway
└── dashboard.html      # Real-time data visualization
