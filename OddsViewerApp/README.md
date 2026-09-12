# OddsViewerApp

## Overview
OddsViewerApp is a data synchronization application designed to manage and update NBA player and team dimensions using the BallDontLie API. The application utilizes GitHub Actions for automated workflows to ensure that the player and team data is kept up-to-date.

## Features
- Syncs player dimensions daily.
- Syncs team dimensions weekly.
- Utilizes GitHub Actions for automation.

## Setup Instructions

### Prerequisites
- Python 3.x
- PostgreSQL database
- Required Python packages (listed in `requirements.txt`)

### Installation
1. Clone the repository:
   ```
   git clone https://github.com/yourusername/OddsViewerApp.git
   ```
2. Navigate to the project directory:
   ```
   cd OddsViewerApp
   ```
3. Install the required packages:
   ```
   pip install -r requirements.txt
   ```

### Environment Variables
Create a `.env` file in the root directory and add the following variables:
```
DB_HOST=your_database_host
DB_PORT=your_database_port
DB_NAME=your_database_name
DB_USER=your_database_user
DB_PASSWORD=your_database_password
BALLDONTLIE_API_KEY=your_api_key
```

### Running the Application
To run the application, execute the following command:
```
python -m ETLService.SyncDim
```

### GitHub Actions Workflows
- **Sync All Players Dimension**: This workflow runs daily at 3 AM CST to sync all player dimensions.
- **Sync Teams Dimension**: This workflow runs every Friday at 12 PM CST to sync team dimensions.

## Contributing
Contributions are welcome! Please open an issue or submit a pull request for any enhancements or bug fixes.

## License
This project is licensed under the MIT License. See the LICENSE file for details.