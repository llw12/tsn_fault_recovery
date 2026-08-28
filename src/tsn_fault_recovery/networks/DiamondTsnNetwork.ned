package tsn_fault_recovery.networks;

import inet.common.Module;
import inet.networklayer.configurator.contract.IL3NetworkConfigurator;
import inet.networklayer.configurator.contract.INetworkConfigurator;
import inet.node.ethernet.EthernetLink;
import inet.node.tsn.TsnDevice;
import inet.node.tsn.TsnSwitch;
import inet.visualizer.contract.IIntegratedVisualizer;
import inet.common.scenario.ScenarioManager;
import tsn_fault_recovery.control.ProfileSwitcher;

network DiamondTsnNetwork extends Module
{
    parameters:
        bool hasGlobalArp = default(true);

        *.ipv4.arp.typename =
            default(hasGlobalArp ? "GlobalArp" : "Arp");

    submodules:

        source: TsnDevice {
            @display("p=100,250");
        }

        s1: TsnSwitch {
            @display("p=300,250");
        }

        s2: TsnSwitch {
            @display("p=500,150");
        }

        s3: TsnSwitch {
            @display("p=500,350");
        }

        s4: TsnSwitch {
            @display("p=700,250");
        }

        destination: TsnDevice {
            @display("p=900,250");
        }

        visualizer:
            <default(firstAvailableOrEmpty("IntegratedCanvasVisualizer"))>
            like IIntegratedVisualizer
            if typename != ""
        {
            @display("p=100,80;is=s");
        }

        configurator:
            <default("Ipv4NetworkConfigurator")>
            like IL3NetworkConfigurator
            if typename != ""
        {
            @display("p=100,130;is=s");
        }

        macForwardingTableConfigurator:
            <default(hasGlobalArp ?
                "MacForwardingTableConfigurator" : "")>
            like INetworkConfigurator
            if typename != ""
        {
            @display("p=100,180;is=s");
        }

        scenarioManager: ScenarioManager {
    		@display("p=220,80;is=s");
		}

        profileSwitcher: ProfileSwitcher {
    		@display("p=300,80;is=s");
		}

    connections:

        source.ethg++ <--> EthernetLink <--> s1.ethg++;

        s1.ethg++ <--> EthernetLink <--> s2.ethg++;
        s1.ethg++ <--> EthernetLink <--> s3.ethg++;

        s2.ethg++ <--> EthernetLink <--> s4.ethg++;
        s3.ethg++ <--> EthernetLink <--> s4.ethg++;

        s4.ethg++ <--> EthernetLink <--> destination.ethg++;
}