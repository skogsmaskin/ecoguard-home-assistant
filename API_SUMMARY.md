# EcoGuard API Summary

This document summarizes the EcoGuard Integration API available at https://integration.ecoguard.se/

## Base URL
```
https://integration.ecoguard.se
```

## Authentication

### User Authentication
**POST** `/token`

Authenticate with User ID and Password.

**Request:**
- `User ID` (string)
- `Password` (string)
- `Issue Refresh Token` (boolean, optional)

**Response:**
- Authentication token and tenant information

### Tenant Authentication
**POST** `/token`

Authenticate with Username/Object number, Password, and Domain.

**Request:**
- `Username/Object number` (string)
- `Password` (string)
- `Domain` (string)
- `Issue Refresh Token` (boolean, optional)

**Response:**
- Authentication token and tenant information

### Re-Authenticate Tenant
**POST** `/token`

Re-authenticate using existing credentials.

### Refresh Token
**POST** `/token`

Refresh authentication token.

**Request:**
- `Refresh Token` (string)

**Response:**
- New authentication token

---

## User Management

### Get Current User
**GET** `/api/users/self?restrictDomainsToModule=`

**Request Parameters:**
- `restrictDomainsToModule` (string, optional)

**Response:**
- Current user information

### Update User Password
**POST** `/api/users/self/password`

**Request:**
- `Password` (string)

### Reset User Password (Tenants Only)
**POST** `/api/users/self/resetUserPW`

**Request:**
- `Email` (string)
- `Domain` (string)
- `ObjectNumber/Username` (string)

### Verify Email (Tenants Only)
**POST** `/api/users/self/verify/email`

**Request:**
- `Email` (string)

### Verify Phone Number (Tenants Only)
**POST** `/api/users/self/verify/phonenumber`

**Request:**
- `Phonenumber` (string)

### Verify Code (Tenants Only)
**POST** `/api/users/self/verify/code`

**Request:**
- `Code` (string)

### Delete Phone Number (Tenants Only)
**DELETE** `/api/users/self/phonenumbers?phonenumber={phonenumber}`

**Request Parameters:**
- `phonenumber` (string)

### Reset Curves User Password
**POST** `/api/users/self/resetCurvesUserPassword?email={email}`

**Request Parameters:**
- `email` (string)

---

## Measuring Devices

### Get Measuring Devices
**GET** `/api/{domaincode}/measuringdevices?externalkey=&internalkey=&status=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `externalkey` (string, optional)
- `internalkey` (string, optional)
- `status` (string, optional, multiple values supported)

**Response:**
- List of measuring devices

### Get Unmounted Measuring Devices
**GET** `/api/{domaincode}/measuringdevices/unmounted?MountTypeCode=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `MountTypeCode` (string, optional, multiple values supported)

**Response:**
- List of unmounted measuring devices

### Get Measuring Device
**GET** `/api/{domaincode}/measuringdevices/{id}`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `id` (path parameter, required) - Measuring device ID

**Response:**
- Measuring device details

### Get Manual Readings
**GET** `/api/{domaincode}/measuringdevices/{id}/registers/{register}/manualreadings`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `id` (path parameter, required) - Measuring device ID
- `register` (path parameter, required)

**Response:**
- Manual reading data

### Save Measurement Values
**POST** `/api/{domaincode}/measuringdevices/{id}/measuringDeviceRegisters/{measuringDeviceRegister}/unixTime/{unixTime}/value/{value}/valueType/{valueType}/measurementValuesSave`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `id` (path parameter, required) - Measuring Device ID
- `measuringDeviceRegister` (path parameter, required)
- `unixTime` (path parameter, required)
- `value` (path parameter, required)
- `valueType` (path parameter, required)

**Response:**
- Save confirmation

### Get Measuring Device Alerts
**GET** `/api/{domaincode}/measuringdevices/{id}/devicealerts`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `id` (path parameter, required) - Measuring device ID

**Response:**
- Device alerts for the measuring device

---

## Device Alerts

### Get Device Alerts
**GET** `/api/{domaincode}/devicealerts?measuringDeviceID=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `measuringDeviceID` (string, optional, multiple values supported)

**Response:**
- List of device alerts

### Get Device Alerts from Node
**GET** `/api/{domaincode}/devicealertsfromnode?nodeID=&includesubnodes=&nodetype=&measuringDeviceID=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `nodeID` (string, optional)
- `includesubnodes` (boolean, optional)
- `nodetype` (string, optional) - Available options: `city`, `area`, `street`, `realestate`, `building`, `entrance`, `apartment`, `premises`
- `measuringDeviceID` (string, optional)

**Response:**
- Device alerts filtered by node

---

## Nodes

### Get Nodes
**GET** `/api/{domaincode}/nodes?nodeid=&nodetypeid=&nodetypecode=&name=&objectnumber=&includesubnodes=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `nodeid` (string, optional)
- `nodetypeid` (string, optional)
- `nodetypecode` (string, optional)
- `name` (string, optional)
- `objectnumber` (string, optional)
- `includesubnodes` (boolean, optional)

**Response:**
- List of nodes

### Get Node
**GET** `/api/{domaincode}/nodes/{id}?includesubnodes=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `id` (path parameter, required) - Node ID
- `includesubnodes` (boolean, optional)

**Response:**
- Node details

---

## Measuring Points

### Get Measuring Points
**GET** `/api/{domaincode}/measuringpoints?nodeid=&includesubnodes=&name=&facilityid=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `nodeid` (string, optional)
- `includesubnodes` (boolean, optional)
- `name` (string, optional)
- `facilityid` (string, optional)

**Response:**
- List of measuring points

### Get Measuring Points for Node
**GET** `/api/{domaincode}/nodes/{id}/measuringpoints?includesubnodes=&name=&facilityid=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `id` (path parameter, required) - Node ID
- `includesubnodes` (boolean, optional)
- `name` (string, optional)
- `facilityid` (string, optional)

**Response:**
- Measuring points for specific node

### Get Measuring Point
**GET** `/api/{domaincode}/measuringpoints/{id}`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `id` (path parameter, required) - Measuring point ID

**Response:**
- Measuring point details

---

## Routing Devices

### Get Routing Device Positions
**GET** `/api/{domaincode}/routingDevicePositions?nodeid=&includesubnodes=&deviceSerial=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `nodeid` (string, optional)
- `includesubnodes` (boolean, optional)
- `deviceSerial` (string, optional)

**Response:**
- Routing device positions

### Get Routing Device Position Properties
**GET** `/api/{domaincode}/routingDevicePositionProperties?routingDevicePositionIds=&includeProperties=&deviceSerial=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `routingDevicePositionIds` (string, optional) - Comma-separated list
- `deviceSerial` (string, optional)
- `includeProperties` (string, optional)

**Response:**
- Routing device position properties

### Get Routing Device Reception Alarms
**GET** `/api/{domaincode}/routingDeviceReceptionAlarms?from=&to=&nodeid=&includesubnodes=&routingDevicePositionID=&deviceSerial=&nodeType=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `from` (datetime, optional)
- `to` (datetime, optional)
- `nodeid` (string, optional)
- `includesubnodes` (boolean, optional)
- `routingDevicePositionID` (string, optional)
- `deviceSerial` (string, optional)
- `nodeType` (string, optional) - Available options: `city`, `area`, `street`, `realestate`, `building`, `entrance`, `apartment`, `premises`

**Response:**
- Routing device reception alarms

### Get Latest Reception
**GET** `/api/{domaincode}/latestReception?nodeid=&includesubnodes=&positionID=&routingDevicePositionID=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `nodeid` (string, optional)
- `includesubnodes` (boolean, optional)
- `positionID` (string, optional)
- `routingDevicePositionID` (string, optional)

**Response:**
- Latest reception data

---

## Installations

### Get Installations
**GET** `/api/{domaincode}/installations?nodeid=&measuringpointid=&includeLatestReceptionAlarm=&devicetype=&from=&to=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `nodeid` (string, optional)
- `measuringpointid` (string, optional)
- `includeLatestReceptionAlarm` (boolean, optional)
- `devicetype` (string, optional, multiple values supported)
- `from` (datetime, optional)
- `to` (datetime, optional)

**Response:**
- List of installations

---

## Node Types

### Get Node Types
**GET** `/api/{domaincode}/nodetypes`

**Request Parameters:**
- `domaincode` (path parameter, required)

**Response:**
- List of node types

### Get Node Type
**GET** `/api/{domaincode}/nodetypes/{id}`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `id` (path parameter, required) - Node type ID

**Response:**
- Node type details

---

## Billing

### Get Billings
**GET** `/api/{domaincode}/billings`

**Request Parameters:**
- `domaincode` (path parameter, required)

**Response:**
- List of billings

### Get Billing
**GET** `/api/{domaincode}/billings/{id}`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `id` (path parameter, required) - Billing ID

**Response:**
- Billing details

### Get Billing (Transformed)
**GET** `/api/{domaincode}/billings/{id}/xml/{format}`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `id` (path parameter, required) - Billing ID
- `format` (path parameter, required) - Export format (XSLT)

**Response:**
- Transformed billing data

### Get Billing Results
**GET** `/api/{domaincode}/billingresults?resultID=&nodeID=&objectNumber=&contractCode=&startFrom=&startTo=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `resultID` (string, optional, multiple values supported)
- `nodeID` (string, optional, multiple values supported)
- `objectNumber` (string, optional, multiple values supported)
- `contractCode` (string, optional, multiple values supported)
- `startFrom` (datetime, optional)
- `startTo` (datetime, optional)

**Response:**
- Billing results

---

## Data Import

### Import Basic JSON
**POST** `/api/{domaincode}/import/basicjson`

**Request Parameters:**
- `domaincode` (path parameter, required)

**Request Body:**
- `Json Data` (JSON object)

**Response:**
- Import result

### Import Elvaco
**POST** `/api/{domaincode}/import/elvaco`

**Request Parameters:**
- `domaincode` (path parameter, required)

**Request Body:**
- `Elvaco Data` (string)

**Response:**
- Import result

---

## Data

### Get Data
**GET** `/api/{domaincode}/data?groupname=&nodeid=&includesubnodes=&measuringpointid&devicepublickey=&deviceid=&from=&to=&interval=&grouping=&utl=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `groupname` (string, optional)
- `nodeid` (string, optional)
- `includesubnodes` (boolean, optional)
- `measuringpointid` (string, optional)
- `devicepublickey` (string, optional)
- `deviceid` (string, optional)
- `from` (datetime, optional)
- `to` (datetime, optional)
- `interval` (string, optional)
- `grouping` (string, optional)
- `utl` (string, optional, multiple values supported) - Utility codes

**Response:**
- Consumption/usage data

### Get List Data
**GET** `/api/{domaincode}/listdata?listcode=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `listcode` (string, optional)

**Response:**
- List data

---

## Comments

### Get Comments
**GET** `/api/{domaincode}/comments?from=&to=&createdFrom=&createdTo=&text=&priority=&userId=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `from` (datetime, optional)
- `to` (datetime, optional)
- `createdFrom` (datetime, optional)
- `createdTo` (datetime, optional)
- `text` (string, optional)
- `priority` (string, optional, multiple values supported)
- `userId` (string, optional)

**Response:**
- List of comments

### Get Comment
**GET** `/api/{domaincode}/comments/{commentId}`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `commentId` (path parameter, required) - Comment ID

**Response:**
- Comment details

### Create Comment
**POST** `/api/{domaincode}/comments`

**Request Parameters:**
- `domaincode` (path parameter, required)

**Request Body:**
- `Text` (string)
- `Date` (datetime)
- `Priority` (string)
- `RefersTo.Type` (string) - Node or MeasuringPoint
- `RefersTo.ID` (string)

**Response:**
- Created comment

### Update Comment
**PUT** `/api/{domaincode}/comments/{commentId}`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `commentId` (path parameter, required) - Comment ID

**Request Body:**
- `Text` (string)
- `Date` (datetime)

**Response:**
- Updated comment

### Delete Comment
**DELETE** `/api/{domaincode}/comments/{commentId}`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `commentId` (path parameter, required) - Comment ID

**Response:**
- Deletion confirmation

### Get Node Comments
**GET** `/api/{domaincode}/nodes/{id}/comments?includesubnodes=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `id` (path parameter, required) - Node ID
- `includesubnodes` (boolean, optional)

**Response:**
- Comments for the node

### Get Measuring Point Comments
**GET** `/api/{domaincode}/measuringpoints/{id}/Comments`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `id` (path parameter, required) - Measuring point ID

**Response:**
- Comments for the measuring point

---

## Settings

### Get Settings
**GET** `/api/{domaincode}/settings`

**Request Parameters:**
- `domaincode` (path parameter, required)

**Response:**
- Domain settings

### Get Setting
**GET** `/api/{domaincode}/settings/{name}`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `name` (path parameter, required) - Setting name

**Response:**
- Specific setting value

---

## Alarm Settings

### Get Alarm Settings
**GET** `/api/{domaincode}/alarmsettings`

**Request Parameters:**
- `domaincode` (path parameter, required)

**Response:**
- List of alarm settings

### Get Node Alarm Settings
**GET** `/api/{domaincode}/nodes/{nodeID}/alarmsettings?includesubnodes=`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `nodeID` (path parameter, required) - Node ID
- `includesubnodes` (boolean, optional)

**Response:**
- Alarm settings for the node

### Get Alarm Setting
**GET** `/api/{domaincode}/alarmsettings/{id}`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `id` (path parameter, required) - Alarm setting ID

**Response:**
- Alarm setting details

### Get Alarm Setting Notifications
**GET** `/api/{domaincode}/alarmsettings/{id}/notifications`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `id` (path parameter, required) - Alarm setting ID

**Response:**
- Notifications for the alarm setting

### Get Alarm Setting Notification
**GET** `/api/{domaincode}/alarmsettings/{id}/notifications/{notificationId}`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `id` (path parameter, required) - Alarm setting ID
- `notificationId` (path parameter, required) - Notification ID

**Response:**
- Notification details

### Create Alarm Setting Notification
**POST** `/api/{domaincode}/alarmsettings/{id}/notifications`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `id` (path parameter, required) - Alarm setting ID

**Request Body:**
- `Type` (string) - Email
- `Notify` (string)

**Response:**
- Created notification

### Delete Alarm Setting Notification
**DELETE** `/api/{domaincode}/alarmsettings/{id}/notifications/{notificationId}`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `id` (path parameter, required) - Alarm setting ID
- `notificationId` (path parameter, required) - Notification ID

**Response:**
- Deletion confirmation

---

## Projects

### Get Projects
**GET** `/api/projects?includeDone=&includePlanned=&mounter=`

**Request Parameters:**
- `includeDone` (boolean, optional)
- `includePlanned` (boolean, optional)
- `mounter` (string, optional)

**Response:**
- List of projects

### Get Project
**GET** `/api/projects/{id}`

**Request Parameters:**
- `id` (path parameter, required) - Project ID

**Response:**
- Project details

### Get Project Updated
**GET** `/api/projects/{id}/Updated?ignoreReports=`

**Request Parameters:**
- `id` (path parameter, required) - Project ID
- `ignoreReports` (boolean, optional)

**Response:**
- Project update information

### Update Project
**PUT** `/api/projects/{id}`

**Request Parameters:**
- `id` (path parameter, required) - Project ID

**Request Body:**
- `json data` (JSON object)
- `image Files` (files, optional)

**Response:**
- Updated project

### Get Project Location Accesses
**GET** `/api/projects/{projectId}/locationaccesses`

**Request Parameters:**
- `projectId` (path parameter, required) - Project ID

**Response:**
- Location accesses for the project

### Get Project Location Access
**GET** `/api/projects/{projectid}/locationaccesses/{id}`

**Request Parameters:**
- `projectid` (path parameter, required) - Project ID
- `id` (path parameter, required) - Access ID

**Response:**
- Location access details

### Create Project Location Access
**POST** `/api/projects/{projectId}/locationaccesses`

**Request Parameters:**
- `projectId` (path parameter, required) - Project ID

**Request Body:**
- `Node ID` (string)
- `Start` (datetime)
- `End` (datetime)

**Response:**
- Created location access

### Update Project Location Access
**PUT** `/api/projects/{projectId}/locationaccesses/{accessid}`

**Request Parameters:**
- `projectId` (path parameter, required) - Project ID
- `accessid` (path parameter, required) - Access ID

**Request Body:**
- `Node ID` (string)
- `Start` (datetime)
- `End` (datetime)

**Response:**
- Updated location access

### Delete Project Location Access
**DELETE** `/api/projects/{projectId}/locationaccesses/{accessid}`

**Request Parameters:**
- `projectId` (path parameter, required) - Project ID
- `accessid` (path parameter, required) - Access ID

**Response:**
- Deletion confirmation

### Get Project Picture
**GET** `{pictureUrl}`

**Request Parameters:**
- `pictureUrl` (path parameter, required) - Picture URL

**Response:**
- Picture file

---

## Instructions

### Get Instructions
**GET** `/api/instructions`

**Response:**
- List of instructions

---

## Meter Events

### Get Meter Events
**GET** `/api/{domaincode}/meterevents`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `Node ID` (string, optional)
- `From` (datetime, optional)
- `To` (datetime, optional)
- `EventType` (string, optional, multiple values supported)

**Response:**
- List of meter events

### Get Meter Event Subscriptions
**GET** `/api/{domaincode}/metereventssubsciptions`

**Request Parameters:**
- `domaincode` (path parameter, required)

**Response:**
- Meter event subscriptions

### Update Meter Event Subscriptions
**PUT** `/api/{domaincode}/metereventssubsciptions`

**Request Parameters:**
- `domaincode` (path parameter, required)

**Request Body:**
- `json data` (JSON object)

**Response:**
- Updated subscriptions

---

## Utilities

### Get Utilities
**GET** `/api/{domaincode}/utilities`

**Request Parameters:**
- `domaincode` (path parameter, required)

**Response:**
- List of utilities

### Get Utility
**GET** `/api/{domaincode}/utilities/{id}`

**Request Parameters:**
- `domaincode` (path parameter, required)
- `id` (path parameter, required) - Utility ID

**Response:**
- Utility details

---

## Notes

- All endpoints require authentication via the `/token` endpoint
- Most endpoints require a `domaincode` path parameter
- Date/time parameters typically accept ISO 8601 format or Unix timestamps
- Multiple values for query parameters can be specified by repeating the parameter or using comma-separated values (depending on endpoint)
- The API supports filtering by nodes with `includesubnodes` option to include child nodes
- Node types include: `city`, `area`, `street`, `realestate`, `building`, `entrance`, `apartment`, `premises`
