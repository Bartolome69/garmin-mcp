# UNVERIFIED: never built (docs/native-app/DECISIONS.md, milestone 10).
Pod::Spec.new do |s|
  s.name           = 'DaashWidgets'
  s.version        = '0.1.0'
  s.summary        = 'Writes the Daash widget snapshot to the App Group and reloads WidgetKit timelines.'
  s.author         = 'Daash'
  s.homepage       = 'https://daash.run'
  s.license        = { :type => 'Proprietary' }
  s.platforms      = { :ios => '17.0' }
  s.source         = { :git => '' }
  s.static_framework = true
  s.dependency 'ExpoModulesCore'
  s.source_files = '*.swift'
end
