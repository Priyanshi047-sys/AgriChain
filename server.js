const express = require('express');
const jwt = require('jsonwebtoken');
const winston = require('winston');
const helmet = require('helmet');
const { Gateway, Wallets } = require('fabric-network');
const path = require('path');
const fs = require('fs');
require('dotenv').config();

const app = express();

// ────────────────────────────────────────────────────────────────
// FEATURE: JSON MIDDLEWARE & SECURITY (Helmet)
// ────────────────────────────────────────────────────────────────
app.use(express.json());
app.use(helmet()); 

// ────────────────────────────────────────────────────────────────
// FEATURE: STRUCTURED ERROR LOGGING (Winston)
// ────────────────────────────────────────────────────────────────
const logger = winston.createLogger({
    level: 'info',
    format: winston.format.combine(
        winston.format.timestamp(),
        winston.format.json()
    ),
    transports: [
        new winston.transports.File({ filename: 'logs/error.log', level: 'error' }),
        new winston.transports.File({ filename: 'logs/combined.log' }),
        new winston.transports.Console({ format: winston.format.simple() })
    ]
});

// ────────────────────────────────────────────────────────────────
// FEATURE: API KEY MIDDLEWARE
// ────────────────────────────────────────────────────────────────
const apiKeyMiddleware = (req, res, next) => {
    const apiKey = req.header('x-api-key');
    if (apiKey && apiKey === process.env.NODEJS_API_KEY) {
        next();
    } else {
        logger.error({ event: 'AUTH_FAIL', reason: 'Invalid API Key', ip: req.ip });
        res.status(401).json({ error: 'Unauthorized: Invalid API Key' });
    }
};

// ────────────────────────────────────────────────────────────────
// FEATURE: JWT TOKEN GENERATION & AUTO-REFRESH LOGIC
// ────────────────────────────────────────────────────────────────
app.post('/api/auth/login', apiKeyMiddleware, (req, res) => {
    // Generate token with 1 hour expiry
    const token = jwt.sign({ device: 'AgriValidator' }, process.env.JWT_SECRET, { expiresIn: '1h' });
    
    // Feature: Auto-refresh would be handled by the client (Python) 
    // requesting a new token using this endpoint before the old one expires.
    res.json({ token });
});

const verifyJWT = (req, res, next) => {
    const token = req.headers['authorization']?.split(' ')[1];
    if (!token) return res.status(403).json({ error: 'No token provided' });

    jwt.verify(token, process.env.JWT_SECRET, (err, decoded) => {
        if (err) return res.status(401).json({ error: 'Token expired/invalid' });
        req.user = decoded;
        next();
    });
};

// ────────────────────────────────────────────────────────────────
// FEATURE: RETRY WITH EXPONENTIAL BACKOFF
// ────────────────────────────────────────────────────────────────
async function submitWithRetry(contract, functionName, args, retries = 3, delay = 1000) {
    try {
        return await contract.submitTransaction(functionName, ...args);
    } catch (error) {
        if (retries <= 0) throw error;
        const nextDelay = delay * 2;
        logger.warn(`Blockchain Busy. Retrying in ${delay}ms... Attempts left: ${retries}`);
        await new Promise(res => setTimeout(res, delay));
        return submitWithRetry(contract, functionName, args, retries - 1, nextDelay);
    }
}

// ────────────────────────────────────────────────────────────────
// FEATURE: POST/SUBMIT -> DATA TO CHAINCODE
// ────────────────────────────────────────────────────────────────
app.post('/api/sensor', apiKeyMiddleware, async (req, res) => {
    const data = req.body;

    // FEATURE: SCHEMA VERSION FIELD
    data.schema_version = "1.1.0"; 

    let gateway;

    try {
        // Setup Connection
        const ccpPath = path.resolve(__dirname, 'connection.json');
        const ccp = JSON.parse(fs.readFileSync(ccpPath, 'utf8'));
        const walletPath = path.join(__dirname, 'wallet');
        const wallet = await Wallets.newFileSystemWallet(walletPath);

        gateway = new Gateway();
        await gateway.connect(ccp, {
            wallet,
            identity: 'appUser',
            discovery: { enabled: true, asLocalhost: true }
        });

        const network = await gateway.getNetwork('mychannel');
        const contract = network.getContract('agrichain');

        // ────────────────────────────────────────────────────────
        // FEATURE: DUPLICATE BATCH ID CHECK
        // ────────────────────────────────────────────────────────
        // We query the ledger for the record_uuid before submitting
        const existsBuffer = await contract.evaluateTransaction('queryReading', data.record_uuid);
        if (existsBuffer.toString()) {
            logger.error({ event: 'DUPLICATE_ID', id: data.record_uuid });
            return res.status(409).json({ error: 'Duplicate Batch ID: Record already exists on Ledger' });
        }

        // ────────────────────────────────────────────────────────
        // FEATURE: SUBMIT TRANSACTION()
        // ────────────────────────────────────────────────────────
        logger.info(`Submitting to Chaincode: ${data.record_uuid}`);
        
        await submitWithRetry(contract, 'createReading', [
            data.record_uuid, 
            JSON.stringify(data)
        ]);

        res.status(200).json({ 
            status: 'Success', 
            message: 'Data anchored to Blockchain',
            record_id: data.record_uuid 
        });

    } catch (error) {
        logger.error({ event: 'CHAINCODE_ERROR', error: error.message });
        res.status(500).json({ error: 'Blockchain submission failed' });
    } finally {
        // ────────────────────────────────────────────────────────
        // FEATURE: GATEWAY.DISCONNECT()
        // ────────────────────────────────────────────────────────
        if (gateway) {
            await gateway.disconnect();
            logger.info('Gateway disconnected.');
        }
    }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
    console.log(`\n🚀 Node.js Backend Running on http://localhost:${PORT}`);
    console.log(`📁 Logs: backend/logs/combined.log`);
});
// ────────────────────────────────────────────────────────────────
// FEATURE: GET /api/sensor/:deviceId — Query from blockchain
// Dashboard uses this to fetch sensor data by device ID
// ────────────────────────────────────────────────────────────────
app.get('/api/sensor/:recordId', apiKeyMiddleware, async (req, res) => {
    const { recordId } = req.params;
    let gateway;
    try {
        const ccpPath = path.resolve(__dirname, 'connection.json');
        const ccp = JSON.parse(fs.readFileSync(ccpPath, 'utf8'));
        const walletPath = path.join(__dirname, 'wallet');
        const wallet = await Wallets.newFileSystemWallet(walletPath);
        gateway = new Gateway();
        await gateway.connect(ccp, {
            wallet,
            identity: 'appUser',
            discovery: { enabled: true, asLocalhost: true }
        });
        const network = await gateway.getNetwork('mychannel');
        const contract = network.getContract('agrichain');
        const result = await contract.evaluateTransaction('QueryReading', recordId);
        res.status(200).json(JSON.parse(result.toString()));
    } catch (error) {
        logger.error({ event: 'QUERY_ERROR', error: error.message });
        res.status(500).json({ error: 'Query failed', detail: error.message });
    } finally {
        if (gateway) await gateway.disconnect();
    }
});

// ────────────────────────────────────────────────────────────────
// FEATURE: GET /api/history/:recordId — Full audit trail
// ────────────────────────────────────────────────────────────────
app.get('/api/history/:recordId', apiKeyMiddleware, async (req, res) => {
    const { recordId } = req.params;
    let gateway;
    try {
        const ccpPath = path.resolve(__dirname, 'connection.json');
        const ccp = JSON.parse(fs.readFileSync(ccpPath, 'utf8'));
        const walletPath = path.join(__dirname, 'wallet');
        const wallet = await Wallets.newFileSystemWallet(walletPath);
        gateway = new Gateway();
        await gateway.connect(ccp, {
            wallet,
            identity: 'appUser',
            discovery: { enabled: true, asLocalhost: true }
        });
        const network = await gateway.getNetwork('mychannel');
        const contract = network.getContract('agrichain');
        const result = await contract.evaluateTransaction('GetHistory', recordId);
        res.status(200).json(JSON.parse(result.toString()));
    } catch (error) {
        logger.error({ event: 'HISTORY_ERROR', error: error.message });
        res.status(500).json({ error: 'History query failed', detail: error.message });
    } finally {
        if (gateway) await gateway.disconnect();
    }
});
