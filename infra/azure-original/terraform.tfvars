location            = "Australia East"
resource_group_name = "koalatech-week07-rg"

# Replace with a unique name for your Azure Container Registry 
acr_name             = "UNIQUE_ACR_NAME"

# Replace with a unique name for your Azure Storage Account
storage_account_name = "STORAGE_ACCOUNT_NAME"

environment = "development"

tags = {
    Project    = "KoalaTech Course Platform"
    ManagedBy  = "Terraform"
    Practical  = "Week07"
    Environment = "Development"
}
