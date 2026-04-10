package main

import (
	"encoding/json"
	"fmt"
	"time"

	"github.com/hyperledger/fabric-contract-api-go/contractapi"
)

type AgriContract struct {
	contractapi.Contract
}

type SensorReading struct {
	RecordUUID    string  `json:"record_uuid"`
	DeviceID      string  `json:"device_id"`
	Temperature   float64 `json:"temp"`
	SoilMoisture  int     `json:"soil"`
	Timestamp     string  `json:"ts"`
	Nonce         string  `json:"nonce"`
	HMAC          string  `json:"hmac"`
	JWTToken      string  `json:"jwt_token"`
	SchemaVersion string  `json:"schema_version"`
	BatchID       string  `json:"batch_id"`
}

func (a *AgriContract) CreateReading(ctx contractapi.TransactionContextInterface, recordUUID string, dataJSON string) error {
	existing, err := ctx.GetStub().GetState(recordUUID)
	if err != nil {
		return fmt.Errorf("failed to read ledger for batch ID %s: %v", recordUUID, err)
	}
	if existing != nil {
		return fmt.Errorf("batch ID %s already exists on the ledger — duplicate rejected", recordUUID)
	}

	var reading SensorReading
	err = json.Unmarshal([]byte(dataJSON), &reading)
	if err != nil {
		return fmt.Errorf("batch ID %s — invalid JSON payload: %v", recordUUID, err)
	}

	if reading.SchemaVersion == "" {
		return fmt.Errorf("batch ID %s — schema_version field is missing", recordUUID)
	}

	txTime, err := ctx.GetStub().GetTxTimestamp()
	if err != nil {
		return fmt.Errorf("batch ID %s — could not get tx timestamp: %v", recordUUID, err)
	}
	reading.Timestamp = time.Unix(txTime.Seconds, 0).UTC().Format(time.RFC3339)
	reading.RecordUUID = recordUUID

	readingBytes, err := json.Marshal(reading)
	if err != nil {
		return fmt.Errorf("batch ID %s — JSON serialization failed: %v", recordUUID, err)
	}

	err = ctx.GetStub().PutState(recordUUID, readingBytes)
	if err != nil {
		return fmt.Errorf("batch ID %s — failed to write to ledger: %v", recordUUID, err)
	}

	return nil
}

func (a *AgriContract) QueryReading(ctx contractapi.TransactionContextInterface, recordUUID string) (*SensorReading, error) {
	readingBytes, err := ctx.GetStub().GetState(recordUUID)
	if err != nil {
		return nil, fmt.Errorf("batch ID %s — ledger read failed: %v", recordUUID, err)
	}
	if readingBytes == nil {
		return nil, fmt.Errorf("batch ID %s — record not found on ledger", recordUUID)
	}

	var reading SensorReading
	err = json.Unmarshal(readingBytes, &reading)
	if err != nil {
		return nil, fmt.Errorf("batch ID %s — JSON deserialization failed: %v", recordUUID, err)
	}

	return &reading, nil
}

type HistoryEntry struct {
	TxID      string         `json:"tx_id"`
	Timestamp string         `json:"timestamp"`
	IsDeleted bool           `json:"is_deleted"`
	Record    *SensorReading `json:"record"`
}

func (a *AgriContract) GetHistory(ctx contractapi.TransactionContextInterface, recordUUID string) ([]HistoryEntry, error) {
	iterator, err := ctx.GetStub().GetHistoryForKey(recordUUID)
	if err != nil {
		return nil, fmt.Errorf("batch ID %s — history fetch failed: %v", recordUUID, err)
	}
	defer iterator.Close()

	var history []HistoryEntry

	for iterator.HasNext() {
		entry, err := iterator.Next()
		if err != nil {
			return nil, fmt.Errorf("batch ID %s — history iterator error: %v", recordUUID, err)
		}

		histEntry := HistoryEntry{
			TxID:      entry.TxId,
			Timestamp: time.Unix(entry.Timestamp.Seconds, 0).UTC().Format(time.RFC3339),
			IsDeleted: entry.IsDelete,
		}

		if !entry.IsDelete && entry.Value != nil {
			var reading SensorReading
			if err := json.Unmarshal(entry.Value, &reading); err == nil {
				histEntry.Record = &reading
			}
		}

		history = append(history, histEntry)
	}

	if len(history) == 0 {
		return nil, fmt.Errorf("batch ID %s — no history found on ledger", recordUUID)
	}

	return history, nil
}

func main() {
	chaincode, err := contractapi.NewChaincode(&AgriContract{})
	if err != nil {
		fmt.Printf("Error creating AgriChain chaincode: %v\n", err)
		return
	}
	if err := chaincode.Start(); err != nil {
		fmt.Printf("Error starting AgriChain chaincode: %v\n", err)
	}
}
