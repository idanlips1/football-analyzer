@description('Base name for all resources')
param baseName string = 'football-hl'

@description('Location for all resources')
param location string = resourceGroup().location

@description('Container image for API and Worker')
param containerImage string

@description('Existing Azure Key Vault name containing app secrets')
param keyVaultName string

// Storage Account — blob, queue, table
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${replace(baseName, '-', '')}storage'
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
  }
}

// Blob containers
resource videosContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${storageAccount.name}/default/videos'
  properties: { publicAccess: 'None' }
}

resource pipelineContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${storageAccount.name}/default/pipeline'
  properties: { publicAccess: 'None' }
}

resource highlightsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${storageAccount.name}/default/highlights'
  properties: { publicAccess: 'None' }
}

// Blob lifecycle — delete videos/ after 30 days
resource blobLifecyclePolicy 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  name: '${storageAccount.name}/default'
  dependsOn: [videosContainer]
  properties: {
    policy: {
      rules: [
        {
          name: 'delete-old-videos'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: ['blockBlob']
              prefixMatch: ['videos/']
            }
            actions: {
              baseBlob: {
                delete: { daysAfterModificationGreaterThan: 30 }
              }
            }
          }
        }
      ]
    }
  }
}

// Queue
resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  name: '${storageAccount.name}/default'
}

resource jobQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  name: '${storageAccount.name}/default/job-queue'
  dependsOn: [queueService]
}

// Table
resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  name: '${storageAccount.name}/default'
}

resource jobsTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  name: '${storageAccount.name}/default/jobs'
  dependsOn: [tableService]
}

// Container Registry
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: '${replace(baseName, '-', '')}acr'
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: true }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

// Log Analytics for Container Apps
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${baseName}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// Container Apps Environment
resource acaEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${baseName}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

var storageConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=core.windows.net'

var sharedEnv = [
  { name: 'STORAGE_BACKEND', value: 'azure' }
  { name: 'AZURE_STORAGE_CONNECTION_STRING', secretRef: 'storage-conn' }
  { name: 'ASSEMBLYAI_API_KEY', secretRef: 'assemblyai-key' }
  { name: 'API_FOOTBALL_KEY', secretRef: 'api-football-key' }
  { name: 'OPENAI_API_KEY', secretRef: 'openai-key' }
  { name: 'API_KEYS', secretRef: 'api-keys' }
]

var sharedSecrets = [
  { name: 'storage-conn', value: storageConnectionString }
  {
    name: 'assemblyai-key'
    keyVaultUrl: 'https://${keyVault.name}.vault.azure.net/secrets/assemblyai-api-key'
    identity: 'system'
  }
  {
    name: 'api-football-key'
    keyVaultUrl: 'https://${keyVault.name}.vault.azure.net/secrets/api-football-key'
    identity: 'system'
  }
  {
    name: 'openai-key'
    keyVaultUrl: 'https://${keyVault.name}.vault.azure.net/secrets/openai-api-key'
    identity: 'system'
  }
  {
    name: 'api-keys'
    keyVaultUrl: 'https://${keyVault.name}.vault.azure.net/secrets/api-keys'
    identity: 'system'
  }
  { name: 'acr-password', value: acr.listCredentials().passwords[0].value }
]

var acrPullRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)
var kvSecretsUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4633458b-17de-408a-b874-0445c86b69e6'
)

// API Container App
resource apiApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${baseName}-api'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
      }
      secrets: sharedSecrets
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: containerImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: sharedEnv
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

// Worker Container App
resource workerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${baseName}-worker'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      secrets: sharedSecrets
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: containerImage
          command: ['python', '-m', 'worker']
          resources: {
            cpu: json('2.0')
            memory: '4Gi'
            ephemeralStorage: '20Gi'
          }
          env: sharedEnv
          volumeMounts: [
            { volumeName: 'tmp', mountPath: '/tmp/football-analyzer' }
          ]
        }
      ]
      volumes: [
        { name: 'tmp', storageType: 'EmptyDir' }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

resource apiAcrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, apiApp.id, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: acrPullRoleId
    principalId: apiApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource workerAcrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, workerApp.id, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: acrPullRoleId
    principalId: workerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource apiKvSecretsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, apiApp.id, kvSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: kvSecretsUserRoleId
    principalId: apiApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource workerKvSecretsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, workerApp.id, kvSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: kvSecretsUserRoleId
    principalId: workerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output apiUrl string = 'https://${apiApp.properties.configuration.ingress.fqdn}'
output acrLoginServer string = acr.properties.loginServer
