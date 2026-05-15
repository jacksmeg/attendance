using System;
using System.IO;
using System.Linq;
using System.Reflection;

public static class Program
{
    private const string ClientDir = @"C:\Program Files\Morpho\MorphoManager\Client";

    public static int Main(string[] args)
    {
        try
        {
            AppDomain.CurrentDomain.AssemblyResolve += ResolveFromClientDir;

            var coreAssembly = Assembly.LoadFrom(Path.Combine(ClientDir, "P6.dll"));
            var p6Assembly = Assembly.LoadFrom(Path.Combine(ClientDir, "P6.Client.dll"));
            var commonAssembly = Assembly.LoadFrom(Path.Combine(ClientDir, "ID1.MM.Client.Common.dll"));

            var initArgsType = commonAssembly.GetType("ID1.MM.Client.Common.InitializationArguments", true);
            var coreRuntimeContextType = coreAssembly.GetType("P6.RuntimeContext", true);
            var runtimeContextType = p6Assembly.GetType("P6.Client.RuntimeContext", true);
            var instanceConfigType = p6Assembly.GetType("P6.Client.InstanceContextConfiguration", true);

            var init = Activator.CreateInstance(initArgsType);
            InitializeCoreRuntime(coreAssembly, coreRuntimeContextType, initArgsType, init);
            var initializeMethod = runtimeContextType.GetMethod("Initialize", BindingFlags.Public | BindingFlags.Static);
            if (initializeMethod != null)
            {
                initializeMethod.Invoke(null, new[] { init });
            }

            var initializedProperty = runtimeContextType.GetProperty("Initialized", BindingFlags.Public | BindingFlags.Static);
            Console.WriteLine("initialized=" + (initializedProperty == null ? "" : initializedProperty.GetValue(null)));

            var mode = args.Length > 0 ? args[0].Trim().ToLowerInvariant() : "config";
            var username = args.Length > 1 ? args[1] : "";
            var password = args.Length > 2 ? args[2] : "";
            var extra = args.Length > 3 ? args[3] : "";
            if (mode == "config" || mode == "session")
            {
                return ProbeConfig(commonAssembly, init, initArgsType, runtimeContextType, instanceConfigType, username, password);
            }
            if (mode == "change-password")
            {
                return ChangePassword(commonAssembly, init, initArgsType, runtimeContextType, instanceConfigType, username, password, extra);
            }
            if (mode == "acquisition-check")
            {
                return AcquisitionCheck();
            }
            if (mode == "template-config")
            {
                return TemplateConfig(commonAssembly, init, initArgsType, runtimeContextType, instanceConfigType, username, password);
            }
            if (mode == "list-biometric-devices")
            {
                return ListBiometricDevices(commonAssembly, init, initArgsType, runtimeContextType, instanceConfigType, username, password);
            }

            Console.WriteLine("mode=unknown");
            return 2;
        }
        catch (Exception ex)
        {
            PrintException("fatal", ex);
            return 1;
        }
    }

    private static void InitializeCoreRuntime(
        Assembly coreAssembly,
        Type coreRuntimeContextType,
        Type initArgsType,
        object init)
    {
        var environmentType = coreAssembly.GetType("P6.RuntimeEnvironmentEnum", true);
        var purposeType = coreAssembly.GetType("P6.RuntimePurposeEnum", true);
        var initializeMethod = coreRuntimeContextType.GetMethod("Initialize", BindingFlags.Public | BindingFlags.Static);
        if (initializeMethod == null)
        {
            return;
        }

        var environment = Enum.Parse(environmentType, "Console", ignoreCase: false);
        var purpose = Enum.Parse(purposeType, "Release", ignoreCase: false);
        var productId = initArgsType.GetProperty("ProductID", BindingFlags.Public | BindingFlags.Instance).GetValue(init);
        var productName = initArgsType.GetProperty("ProductName", BindingFlags.Public | BindingFlags.Instance).GetValue(init);

        initializeMethod.Invoke(null, new[] { environment, purpose, productId, productName, init });
    }

    private static int ListBiometricDevices(
        Assembly commonAssembly,
        object init,
        Type initArgsType,
        Type runtimeContextType,
        Type instanceConfigType,
        string username,
        string password)
    {
        try
        {
            Console.WriteLine("probe=list-biometric-devices");
            var loadMethod = instanceConfigType.GetMethod(
                "Load",
                BindingFlags.Public | BindingFlags.Static,
                binder: null,
                types: new[] { typeof(string) },
                modifiers: null
            );
            var config = loadMethod.Invoke(null, new object[] { ClientDir });
            var serviceNameProperty = runtimeContextType.GetProperty("ServiceName", BindingFlags.Public | BindingFlags.Static);
            var serviceName = serviceNameProperty == null ? null : serviceNameProperty.GetValue(null);

            var createSessionClient = initArgsType.GetMethod("CreateSessionServiceClient", BindingFlags.Public | BindingFlags.Instance);
            var createEntityClient = initArgsType.GetMethod("CreateEntityServiceClient", BindingFlags.Public | BindingFlags.Instance);
            var args = createSessionClient.GetParameters()
                .Select(p => BuildSessionClientArgument(config, p.Name, serviceName))
                .ToArray();
            var sessionClient = createSessionClient.Invoke(init, args);
            var entityClient = createEntityClient.Invoke(init, args);
            Console.WriteLine("session_client_created=" + (sessionClient != null));
            Console.WriteLine("entity_client_created=" + (entityClient != null));

            if (string.IsNullOrWhiteSpace(username) || string.IsNullOrWhiteSpace(password))
            {
                Console.WriteLine("list_status=missing_credentials");
                return 12;
            }

            var loginMethod = initArgsType.GetMethod("SessionServiceLogin", BindingFlags.Public | BindingFlags.Instance);
            var credential = CreateCredential(commonAssembly, username, password, false);
            var loginResponse = loginMethod.Invoke(init, new[] { sessionClient, credential });
            var sessionToken = GetProperty(loginResponse, "SessionToken");
            Console.WriteLine("login_status=success");

            var modelType = commonAssembly.GetType("MorphoManager.BiometricDeviceModel", true);
            var filterType = commonAssembly.GetType("MorphoManager.BiometricDeviceModelFilter", true);
            var sortType = commonAssembly.GetType("MorphoManager.BiometricDeviceModelSortOrder", true);
            var filter = Activator.CreateInstance(filterType);
            SetProperty(filter, "ReturnCount", 100);
            SetProperty(filter, "ReturnFromStartIndex", 0);
            SetProperty(filter, "ComputeFilteredCount", true);
            SetProperty(filter, "ComputeUnfilteredCount", true);
            var sort = Activator.CreateInstance(sortType);

            var genericMethod = initArgsType.GetMethods(BindingFlags.Public | BindingFlags.Instance)
                .First(m => m.Name == "EntityServiceLoadByEntityFilter" && m.IsGenericMethod);
            var typedMethod = genericMethod.MakeGenericMethod(modelType);
            var result = typedMethod.Invoke(init, new[] { entityClient, sessionToken, filter, sort });

            PrintProperty(result, "FilteredTotal", "filtered_total");
            PrintProperty(result, "UnfilteredTotal", "unfiltered_total");

            var items = GetProperty(result, "Items") as System.Collections.IEnumerable;
            var index = 0;
            if (items != null)
            {
                foreach (var item in items)
                {
                    Console.WriteLine("device[" + index + "].Name=" + FormatValue(GetProperty(item, "Name")));
                    Console.WriteLine("device[" + index + "].SerialNumber=" + FormatValue(GetProperty(item, "SerialNumber")));
                    Console.WriteLine("device[" + index + "].DeviceType=" + FormatValue(GetProperty(item, "DeviceType")));
                    Console.WriteLine("device[" + index + "].HardwareFamily=" + FormatValue(GetProperty(item, "HardwareFamily")));
                    Console.WriteLine("device[" + index + "].Status=" + FormatValue(GetProperty(item, "Status")));
                    Console.WriteLine("device[" + index + "].IPAddressHostname=" + FormatValue(GetProperty(item, "IPAddressHostname")));
                    Console.WriteLine("device[" + index + "].Port=" + FormatValue(GetProperty(item, "Port")));
                    Console.WriteLine("device[" + index + "].ErrorMessage=" + FormatValue(GetProperty(item, "ErrorMessage")));
                    index++;
                }
            }
            Console.WriteLine("item_count=" + index);
            return 0;
        }
        catch (Exception ex)
        {
            PrintException("list_devices_error", ex);
            return 13;
        }
    }

    private static int TemplateConfig(
        Assembly commonAssembly,
        object init,
        Type initArgsType,
        Type runtimeContextType,
        Type instanceConfigType,
        string username,
        string password)
    {
        try
        {
            Console.WriteLine("probe=template-config");
            var loadMethod = instanceConfigType.GetMethod(
                "Load",
                BindingFlags.Public | BindingFlags.Static,
                binder: null,
                types: new[] { typeof(string) },
                modifiers: null
            );
            var config = loadMethod.Invoke(null, new object[] { ClientDir });
            var serviceNameProperty = runtimeContextType.GetProperty("ServiceName", BindingFlags.Public | BindingFlags.Static);
            var serviceName = serviceNameProperty == null ? null : serviceNameProperty.GetValue(null);

            var createSessionClient = initArgsType.GetMethod("CreateSessionServiceClient", BindingFlags.Public | BindingFlags.Instance);
            var createFunctionClient = initArgsType.GetMethod("CreateFunctionServiceClient", BindingFlags.Public | BindingFlags.Instance);
            var args = createSessionClient.GetParameters()
                .Select(p => BuildSessionClientArgument(config, p.Name, serviceName))
                .ToArray();
            var sessionClient = createSessionClient.Invoke(init, args);
            var functionClient = createFunctionClient.Invoke(init, args);
            Console.WriteLine("session_client_created=" + (sessionClient != null));
            Console.WriteLine("function_client_created=" + (functionClient != null));

            if (string.IsNullOrWhiteSpace(username) || string.IsNullOrWhiteSpace(password))
            {
                Console.WriteLine("template_config_status=missing_credentials");
                return 10;
            }

            var loginMethod = initArgsType.GetMethod("SessionServiceLogin", BindingFlags.Public | BindingFlags.Instance);
            var credential = CreateCredential(commonAssembly, username, password, false);
            var loginResponse = loginMethod.Invoke(init, new[] { sessionClient, credential });
            var session = GetProperty(loginResponse, "Session");
            var sessionToken = GetProperty(loginResponse, "SessionToken");
            Console.WriteLine("login_status=success");
            PrintProperty(session, "IssuedToConsumerID", "issued_to_consumer_id");

            var requestType = commonAssembly.GetType("MorphoManager.TemplateCaptureConfigurationFunctionArguments", true);
            var request = Activator.CreateInstance(requestType);
            var consumerId = GetProperty(session, "IssuedToConsumerID");
            SetProperty(request, "ClientID", consumerId ?? Guid.NewGuid());

            var functionRunMethod = initArgsType.GetMethod("FunctionServiceRun", BindingFlags.Public | BindingFlags.Instance);
            var response = functionRunMethod.Invoke(init, new[] { functionClient, sessionToken, request });

            foreach (var property in response.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance))
            {
                var value = property.GetValue(response, null);
                Console.WriteLine("config_" + property.Name + "=" + FormatValue(value));
            }
            return 0;
        }
        catch (Exception ex)
        {
            PrintException("template_config_error", ex);
            return 11;
        }
    }

    private static int AcquisitionCheck()
    {
        try
        {
            Console.WriteLine("probe=acquisition-check");
            AppDomain.CurrentDomain.AssemblyResolve += ResolveFromClientDir;
            var acquisitionAssembly = Assembly.LoadFrom(Path.Combine(ClientDir, "Morpho.MorphoAcquisition.dll"));
            var loaderType = acquisitionAssembly.GetType("Morpho.MorphoAcquisition.DeviceLoader", true);
            var deviceType = acquisitionAssembly.GetType("Morpho.MorphoAcquisition.DeviceType", true);
            var createDevice = loaderType.GetMethod("CreateDevice", BindingFlags.Public | BindingFlags.Static);
            var typesToCheck = new[] { "MORPHOSMART", "MORPHOKIT_FVP", "MORPHOKIT" };

            foreach (var typeName in typesToCheck)
            {
                Console.WriteLine("device_type=" + typeName);
                try
                {
                    var enumValue = Enum.Parse(deviceType, typeName, ignoreCase: false);
                    var device = createDevice.Invoke(null, new[] { enumValue });
                    Console.WriteLine("created=" + (device != null));
                    if (device == null)
                    {
                        continue;
                    }

                    var getConnectedDevices = device.GetType().GetMethod("GetConnectedDevices", BindingFlags.Public | BindingFlags.Instance);
                    var connected = getConnectedDevices.Invoke(device, null) as string[];
                    if (connected == null || connected.Length == 0)
                    {
                        Console.WriteLine("device_count=0");
                        continue;
                    }

                    Console.WriteLine("device_count=" + connected.Length);
                    foreach (var found in connected)
                    {
                        Console.WriteLine("connected_device=" + found);
                    }
                }
                catch (Exception ex)
                {
                    PrintException("acquisition_error_" + typeName.ToLowerInvariant(), ex);
                }
            }

            return 0;
        }
        catch (Exception ex)
        {
            PrintException("acquisition_fatal", ex);
            return 9;
        }
    }

    private static int ChangePassword(
        Assembly commonAssembly,
        object init,
        Type initArgsType,
        Type runtimeContextType,
        Type instanceConfigType,
        string username,
        string currentPassword,
        string newPassword)
    {
        try
        {
            Console.WriteLine("probe=change-password");

            var loadMethod = instanceConfigType.GetMethod(
                "Load",
                BindingFlags.Public | BindingFlags.Static,
                binder: null,
                types: new[] { typeof(string) },
                modifiers: null
            );
            var config = loadMethod.Invoke(null, new object[] { ClientDir });
            var createSessionClient = initArgsType.GetMethod(
                "CreateSessionServiceClient",
                BindingFlags.Public | BindingFlags.Instance
            );
            var serviceNameProperty = runtimeContextType.GetProperty("ServiceName", BindingFlags.Public | BindingFlags.Static);
            var serviceName = serviceNameProperty == null ? null : serviceNameProperty.GetValue(null);
            var args = createSessionClient.GetParameters()
                .Select(p => BuildSessionClientArgument(config, p.Name, serviceName))
                .ToArray();
            var sessionClient = createSessionClient.Invoke(init, args);
            Console.WriteLine("session_client_created=" + (sessionClient != null));

            if (string.IsNullOrWhiteSpace(username) || string.IsNullOrWhiteSpace(currentPassword) || string.IsNullOrWhiteSpace(newPassword))
            {
                Console.WriteLine("change_password_status=missing_arguments");
                return 7;
            }

            var currentCredential = CreateCredential(commonAssembly, username, currentPassword, false);
            var newCredential = CreateCredential(commonAssembly, username, newPassword, true);

            var updateMethod = initArgsType.GetMethod(
                "SessionServiceCredentialUpdate",
                BindingFlags.Public | BindingFlags.Instance
            );
            updateMethod.Invoke(init, new[] { sessionClient, currentCredential, newCredential });
            Console.WriteLine("change_password_status=success");
            return 0;
        }
        catch (Exception ex)
        {
            Console.WriteLine("change_password_status=failed");
            PrintException("change_password_error", ex);
            return 8;
        }
    }

    private static int ProbeConfig(
        Assembly commonAssembly,
        object init,
        Type initArgsType,
        Type runtimeContextType,
        Type instanceConfigType,
        string username,
        string password)
    {
        try
        {
            Console.WriteLine("probe=config");

            var loadMethod = instanceConfigType.GetMethod(
                "Load",
                BindingFlags.Public | BindingFlags.Static,
                binder: null,
                types: new[] { typeof(string) },
                modifiers: null
            );
            var config = loadMethod.Invoke(null, new object[] { ClientDir });

            PrintProperty(config, "ServerHostname", "server_hostname");
            PrintProperty(config, "ServerPort", "server_port");
            PrintProperty(config, "WebServiceProtocol", "webservice_protocol");
            PrintProperty(config, "TLSMode", "tls_mode");
            PrintProperty(config, "ServerCertificateAuthenticationMode", "server_cert_auth_mode");
            PrintByteArrayLength(config, "CachedCredential", "cached_credential_bytes");

            var createSessionClient = initArgsType.GetMethod(
                "CreateSessionServiceClient",
                BindingFlags.Public | BindingFlags.Instance
            );
            if (createSessionClient == null)
            {
                Console.WriteLine("session_client_method=missing");
                return 3;
            }

            var serviceNameProperty = runtimeContextType.GetProperty("ServiceName", BindingFlags.Public | BindingFlags.Static);
            var serviceName = serviceNameProperty == null ? null : serviceNameProperty.GetValue(null);

            var args = createSessionClient.GetParameters()
                .Select(p => BuildSessionClientArgument(config, p.Name, serviceName))
                .ToArray();

            var sessionClient = createSessionClient.Invoke(init, args);
            Console.WriteLine("session_client_created=" + (sessionClient != null));

            var createFunctionClient = initArgsType.GetMethod(
                "CreateFunctionServiceClient",
                BindingFlags.Public | BindingFlags.Instance
            );
            object functionClient = null;
            if (createFunctionClient != null)
            {
                functionClient = createFunctionClient.Invoke(init, args);
            }
            Console.WriteLine("function_client_created=" + (functionClient != null));

            if (string.IsNullOrWhiteSpace(username) && string.IsNullOrWhiteSpace(password))
            {
                Console.WriteLine("credential_source=none");
                Console.WriteLine("login_status=not_attempted");
                return 0;
            }

            if (string.IsNullOrWhiteSpace(username) || string.IsNullOrWhiteSpace(password))
            {
                Console.WriteLine("credential_source=explicit");
                Console.WriteLine("login_status=needs_credentials");
                return 5;
            }

            return ProbeLoginAndInventory(commonAssembly, init, initArgsType, sessionClient, functionClient, username, password);
        }
        catch (Exception ex)
        {
            PrintException("config_error", ex);
            return 4;
        }
    }

    private static int ProbeLoginAndInventory(
        Assembly commonAssembly,
        object init,
        Type initArgsType,
        object sessionClient,
        object functionClient,
        string username,
        string password)
    {
        Console.WriteLine("credential_source=explicit");

        try
        {
            var credential = CreateCredential(commonAssembly, username, password, false);

            var loginMethod = initArgsType.GetMethod(
                "SessionServiceLogin",
                BindingFlags.Public | BindingFlags.Instance
            );
            var loginResponse = loginMethod.Invoke(init, new[] { sessionClient, credential });
            var session = GetProperty(loginResponse, "Session");
            var sessionToken = GetProperty(loginResponse, "SessionToken");

            Console.WriteLine("login_status=success");
            Console.WriteLine("session_token_created=" + (sessionToken != null));
            if (session != null)
            {
                PrintProperty(session, "IssuedToConsumerIdentifier", "issued_to_consumer_identifier");
                PrintProperty(session, "IssuedToInstanceApplicationName", "issued_to_instance_application_name");
                PrintProperty(session, "IsClientAdministrator", "is_client_administrator");
                PrintProperty(session, "IsServerAdministrator", "is_server_administrator");
            }

            if (functionClient == null || sessionToken == null)
            {
                return 0;
            }

            var requestSourceType = initArgsType.Assembly.GetType("MorphoManager.RequestSource", true);
            var inventoryArgsType = initArgsType.Assembly.GetType(
                "MorphoManager.GetBiometricDeviceInventoryFunctionArguments",
                true
            );
            var inventoryArguments = Activator.CreateInstance(inventoryArgsType);
            SetProperty(
                inventoryArguments,
                "Source",
                Enum.Parse(requestSourceType, "BiometricDevicePage", ignoreCase: false)
            );

            var functionRunMethod = initArgsType.GetMethod(
                "FunctionServiceRun",
                BindingFlags.Public | BindingFlags.Instance
            );
            var inventoryResponse = functionRunMethod.Invoke(init, new[] { functionClient, sessionToken, inventoryArguments });
            PrintProperty(inventoryResponse, "NumberTerminalSecured", "inventory_terminal_secured");
            PrintProperty(inventoryResponse, "NumberTerminalNotSecured", "inventory_terminal_not_secured");
            PrintProperty(inventoryResponse, "NumberTerminalNotCompatible", "inventory_terminal_not_compatible");

            var inventoryException = GetProperty(inventoryResponse, "Exception") as Exception;
            if (inventoryException != null)
            {
                Console.WriteLine("inventory_exception=" + inventoryException.Message);
            }

            return 0;
        }
        catch (Exception ex)
        {
            Console.WriteLine("login_status=failed");
            PrintException("login_error", ex);
            return 6;
        }
    }

    private static object BuildSessionClientArgument(object config, string parameterName, object serviceName)
    {
        switch (parameterName)
        {
            case "tlsMode":
                return GetProperty(config, "TLSMode");
            case "serverHostname":
                return GetProperty(config, "ServerHostname");
            case "serverPort":
                return GetProperty(config, "ServerPort");
            case "serviceName":
                return serviceName;
            case "webServiceProtocol":
                return GetProperty(config, "WebServiceProtocol");
            case "serverCertificateAuthenticationMode":
                return GetProperty(config, "ServerCertificateAuthenticationMode");
            case "serverCertificateThumbprint":
                return GetProperty(config, "ServerCertificateThumbprint");
            case "serverCertificateIssuerThumbprint":
                return GetProperty(config, "ServerCertificateIssuerThumbprint");
            case "clientCertificateThumbprint":
                return GetProperty(config, "ClientCertificateThumbprint");
            case "certificateRevocationCheckMode":
                return GetProperty(config, "CertificateRevocationCheckMode");
            default:
                throw new InvalidOperationException("Unknown session client parameter: " + parameterName);
        }
    }

    private static object GetProperty(object target, string propertyName)
    {
        var property = target.GetType().GetProperty(propertyName, BindingFlags.Public | BindingFlags.Instance);
        return property == null ? null : property.GetValue(target);
    }

    private static Version GetMorphoManagerVersion(Assembly commonAssembly)
    {
        var fileVersion = System.Diagnostics.FileVersionInfo.GetVersionInfo(commonAssembly.Location).ProductVersion;
        Version version;
        if (!string.IsNullOrWhiteSpace(fileVersion) && Version.TryParse(fileVersion, out version))
        {
            return version;
        }
        return new Version(17, 6, 0, 9);
    }

    private static string FormatValue(object value)
    {
        if (value == null)
        {
            return "";
        }
        var array = value as Array;
        if (array != null && !(value is byte[]))
        {
            var parts = new string[array.Length];
            for (var i = 0; i < array.Length; i++)
            {
                parts[i] = FormatValue(array.GetValue(i));
            }
            return string.Join(",", parts);
        }
        var enumerable = value as System.Collections.IEnumerable;
        if (enumerable != null && !(value is string) && !(value is byte[]))
        {
            var values = new System.Collections.Generic.List<string>();
            foreach (var item in enumerable)
            {
                values.Add(FormatValue(item));
            }
            return string.Join(",", values);
        }
        var bytes = value as byte[];
        if (bytes != null)
        {
            return "byte[" + bytes.Length + "]";
        }
        return value.ToString();
    }

    private static void SetProperty(object target, string propertyName, object value)
    {
        var property = target.GetType().GetProperty(propertyName, BindingFlags.Public | BindingFlags.Instance);
        if (property != null && property.CanWrite)
        {
            property.SetValue(target, value);
        }
    }

    private static object CreateCredential(Assembly commonAssembly, string username, string password, bool isLoginCredentialUpdate)
    {
        var credentialType = commonAssembly.GetType("MorphoManager.UsernamePasswordCredential", true);
        var credential = Activator.CreateInstance(credentialType);
        SetProperty(credential, "Username", username);
        SetProperty(credential, "Password", password);
        SetProperty(credential, "ConsumerComponentName", "AttendanceSystem");
        SetProperty(credential, "InstanceHostname", Environment.MachineName);
        SetProperty(credential, "InstanceID", Guid.NewGuid());
        SetProperty(credential, "ConsumerVersion", GetMorphoManagerVersion(commonAssembly));
        SetProperty(credential, "ConsumerTimeZoneID", TimeZoneInfo.Local.Id);
        SetProperty(credential, "ConsumerLanguageCode", System.Globalization.CultureInfo.CurrentCulture.Name);
        SetProperty(credential, "IsLoginCredentialUpdate", isLoginCredentialUpdate);
        return credential;
    }

    private static void PrintProperty(object target, string propertyName, string label)
    {
        Console.WriteLine(label + "=" + (GetProperty(target, propertyName) ?? ""));
    }

    private static void PrintByteArrayLength(object target, string propertyName, string label)
    {
        var value = GetProperty(target, propertyName) as byte[];
        Console.WriteLine(label + "=" + (value == null ? 0 : value.Length));
    }

    private static Assembly ResolveFromClientDir(object sender, ResolveEventArgs args)
    {
        var simpleName = new AssemblyName(args.Name).Name + ".dll";
        var candidate = Path.Combine(ClientDir, simpleName);
        return File.Exists(candidate) ? Assembly.LoadFrom(candidate) : null;
    }

    private static void PrintException(string prefix, Exception ex)
    {
        var root = ex is TargetInvocationException && ex.InnerException != null ? ex.InnerException : ex;
        Console.WriteLine(prefix + "=" + root.GetType().FullName + ":" + root.Message);
        if (root.InnerException != null)
        {
            Console.WriteLine(
                prefix + "_inner=" + root.InnerException.GetType().FullName + ":" + root.InnerException.Message
            );
        }
    }
}
