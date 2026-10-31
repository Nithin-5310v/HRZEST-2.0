// HRZEST delivery pipeline -- Jenkins on the team's Windows PC
// (setup and day-to-day use: ci/jenkins/README.md).
//
//   develop: lint + tests -> deploy to the development copy (http://localhost:5050)
//   main:    lint + tests -> wait for approval -> deploy that commit to production (EC2)
//
// Production only takes code that already ran in the development copy:
// deploy_prod.ps1 refuses a commit whose files were never deployed there,
// unless the approver ticks "hotfix". GitHub Actions still runs its own
// checks on every PR; the server re-checks them before switching commits.
pipeline {
  agent none
  options {
    // A newer push replaces a build still testing or waiting for approval.
    disableConcurrentBuilds(abortPrevious: true)
    timestamps()
    buildDiscarder(logRotator(numToKeepStr: '30'))
  }
  environment {
    PYTHONUTF8 = '1'
    PYTHONIOENCODING = 'utf-8'
  }
  stages {
    stage('Checks') {
      agent any
      environment {
        VENV = "${env.HRZEST_CI_HOME}\\venvs\\ci"
      }
      stages {
        stage('Python environment') {
          steps {
            script { env.DEPLOY_SHA = env.GIT_COMMIT }
            powershell '.\\ci\\jenkins\\setup_venv.ps1 -Venv $env:VENV -Dev'
          }
        }
        stage('Lint') {
          steps {
            bat '"%VENV%\\Scripts\\python.exe" -m flake8 .'
          }
        }
        stage('Tests') {
          steps {
            bat '"%VENV%\\Scripts\\python.exe" ci\\jenkins\\run_tests.py -n 4'
          }
          post {
            always { junit allowEmptyResults: true, testResults: 'test-results.xml' }
          }
        }
        stage('Ran in development?') {
          when { branch 'main' }
          steps {
            script {
              def status = powershell(returnStatus: true,
                script: '.\\ci\\jenkins\\deploy_prod.ps1 -Commit $env:DEPLOY_SHA -CheckOnly')
              env.DEV_VERIFIED = (status == 0).toString()
            }
          }
        }
      }
    }

    stage('Deploy to development') {
      when { branch 'develop' }
      agent any
      steps {
        powershell '.\\ci\\jenkins\\deploy_dev.ps1 -Commit $env:DEPLOY_SHA'
      }
    }

    stage('Approve production') {
      when { branch 'main' }
      steps {
        script {
          def verified = env.DEV_VERIFIED == 'true'
          def hotfix = timeout(time: 7, unit: 'DAYS') {
            input(
              message: "Deploy ${env.DEPLOY_SHA.take(7)} to production?\n" + (verified
                ? 'This exact code ran in the development copy.'
                : 'WARNING: this code was NOT deployed to the development copy first.'),
              ok: 'Deploy to production',
              parameters: [booleanParam(name: 'HOTFIX', defaultValue: false,
                description: 'Deploy even though it never ran in development (emergency fixes only)')])
          }
          env.HOTFIX = hotfix ? 'true' : 'false'
        }
      }
    }

    stage('Deploy to production') {
      when { branch 'main' }
      agent any
      steps {
        powershell '.\\ci\\jenkins\\deploy_prod.ps1 -Commit $env:DEPLOY_SHA -Hotfix:([bool]::Parse($env:HOTFIX))'
      }
    }
  }
}
